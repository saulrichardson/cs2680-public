import { aggregateNumbers, aggregateSummaries, summarizeRun } from '@/core/derive';
import { applyEnvelopes, createRunView } from '@/core/reducer';
import type {
  CandidateAttempt,
  CompareLane,
  CompareResult,
  FileStat,
  Patch,
  RunStatus,
  RunSummary,
  RunView,
} from '@/core/types';
import type { Db } from '@/server/db';
import * as repo from '@/server/db/repo';
import type { RunRow } from '@/server/db/schema';
import { describeGitFailure, diffAgainstBase } from '@/server/git/compare';

/**
 * How many runs' reduced views are kept. A race is at most six lanes, and a lane's candidate is
 * usually one execution, so this covers several races being watched at once while staying a fixed,
 * explicit bound rather than "however many runs the server has ever compared".
 */
const MAX_CACHED_RUNS = 32;

/** How many `git diff` invocations may run at once, across every comparison in flight. */
const MAX_CONCURRENT_GIT = 4;

/**
 * How long shutdown waits for comparisons already running, *after* cancelling them. Cancellation is
 * what makes the wait short: each in-flight git command is abandoned and its process group reaped,
 * rather than being left to reach its own per-command deadline — and a comparison is many commands
 * across several lanes, which no single command's timeout bounds.
 */
const SHUTDOWN_BUDGET_MS = 40_000;

/**
 * How much folded history the cache may hold, weighed in the UTF-8 bytes of the event lines behind
 * it.
 *
 * An entry count alone is not a memory bound: an imported recording can carry a single very large
 * string, and a long execution's tool output can too, so 32 entries could mean a few kilobytes or
 * hundreds of megabytes.
 *
 * This is a *weight*, not a measurement of the heap. The stored JSON is what the view was folded
 * from and is proportional to it, but the view is objects — strings, blocks, a call graph, the
 * projections built over them — and those carry their own overhead. The number to reason about is
 * "how much recorded history is being held", not "how many bytes of V8 heap".
 */
const MAX_CACHED_BYTES = 32 * 1024 * 1024;

/**
 * A single run bigger than this is never cached. Holding it would evict everything else to keep one
 * entry, and re-folding it costs less than that. The comparison is still correct — it is computed
 * from the raw events either way.
 */
const MAX_CACHEABLE_RUN_BYTES = 8 * 1024 * 1024;

/**
 * Only event-derived facts are cached. Anything that lives on the run row — status, cost, session,
 * `endedAt` — is read fresh on every comparison, so a cached entry can never hold a stale lifecycle.
 */
interface CachedRun {
  lastSeq: number;
  view: RunView;
  summary: RunSummary;
  /** UTF-8 bytes of stored event JSON folded into `view`; see `MAX_CACHED_BYTES`. */
  bytes: number;
}

/** A fixed number of permits, handed out in request order. */
class Gate {
  private free: number;
  private readonly waiting: (() => void)[] = [];
  constructor(permits: number) {
    this.free = permits;
  }
  async run<T>(task: () => Promise<T>): Promise<T> {
    if (this.free > 0) this.free -= 1;
    else await new Promise<void>((resolve) => this.waiting.push(resolve));
    try {
      return await task();
    } finally {
      const next = this.waiting.shift();
      if (next) next();
      else this.free += 1;
    }
  }
}

/**
 * Builds the side-by-side comparison for a fan-out race.
 *
 * Compare is polled every couple of seconds while a race runs, and each poll used to re-read and
 * re-fold every event of every lane from scratch — a whole trajectory's reduction per lane per
 * poll, growing with the run. Two bounded pieces of state fix that without making the result any
 * less current:
 *
 * - a reduced view per run, advanced by the events that arrived since it was last read. The event
 *   log is append-only, so folding the tail onto the previous view gives exactly what folding the
 *   whole log would. The cache is bounded and holds no row-derived fact.
 * - one in-flight comparison per group. A second request for a group already being built waits for
 *   that build instead of starting a duplicate set of git commands against the same worktrees.
 *
 * The service owns both, so their lifetime is the server's and nothing else has to remember to
 * invalidate them.
 */
export class CompareService {
  private readonly cache = new Map<string, CachedRun>();
  private readonly inFlight = new Map<string, Promise<CompareResult>>();
  private readonly gate: Gate;
  private readonly maxCachedRuns: number;

  private readonly maxCachedBytes: number;
  private readonly maxCacheableRunBytes: number;
  private readonly gitOptions: { timeoutMs?: number; bin?: string };
  /** Set by `shutdown`: no new comparison is admitted after that point. */
  private closing = false;
  /** Aborted by `shutdown`, so the git commands of comparisons already running stop being waited on. */
  private readonly cancellation = new AbortController();

  constructor(
    private readonly deps: {
      db: Db;
      maxCachedRuns?: number;
      maxCachedBytes?: number;
      maxCacheableRunBytes?: number;
      gitConcurrency?: number;
      gitTimeoutMs?: number;
      /** Only a test substitutes this, to stand in for a git that hangs. */
      gitBin?: string;
    },
  ) {
    this.maxCachedRuns = deps.maxCachedRuns ?? MAX_CACHED_RUNS;
    this.maxCachedBytes = deps.maxCachedBytes ?? MAX_CACHED_BYTES;
    this.maxCacheableRunBytes = deps.maxCacheableRunBytes ?? MAX_CACHEABLE_RUN_BYTES;
    this.gate = new Gate(deps.gitConcurrency ?? MAX_CONCURRENT_GIT);
    this.gitOptions = { timeoutMs: deps.gitTimeoutMs, bin: deps.gitBin };
  }

  /** For tests and diagnostics: how many reduced views are being held. */
  cachedRunCount(): number {
    return this.cache.size;
  }

  /** For tests and diagnostics: how much folded history is being held, in bytes. */
  cachedBytes(): number {
    let total = 0;
    for (const entry of this.cache.values()) total += entry.bytes;
    return total;
  }

  compare(groupId: string): Promise<CompareResult> {
    if (this.closing) return Promise.reject(new Error('the server is shutting down'));
    const existing = this.inFlight.get(groupId);
    if (existing) return existing;
    const started = this.build(groupId).finally(() => this.inFlight.delete(groupId));
    this.inFlight.set(groupId, started);
    return started;
  }

  /**
   * Stop taking comparisons, cancel the ones already running, and wait for them to settle.
   *
   * A comparison owns git subprocesses and reads through the database, so it has to be finished
   * with both before either goes away. Closing the connection under an in-flight `git diff` fails
   * the read; abandoning it at `process.exit` leaves the subprocess to be cleaned up by nobody.
   *
   * Waiting alone would not be bounded by anything useful. A build runs several commands per lane,
   * lanes queue behind a concurrency gate, and several groups can be building at once — so the
   * per-command deadline bounds a command, not the comparison. Cancellation is therefore explicit:
   * the abort abandons the running commands, each of which reaps its own process group before it
   * settles, and the remaining commands are never started.
   *
   * Answers `false` when a build was still running at the deadline — meaning cleanup could *not* be
   * confirmed, and the caller must say so rather than imply everything was tidied away.
   */
  async shutdown(budgetMs = SHUTDOWN_BUDGET_MS): Promise<boolean> {
    this.closing = true;
    this.cancellation.abort(new Error('the server is shutting down'));
    const deadline = Date.now() + budgetMs;
    while (this.inFlight.size > 0 && Date.now() < deadline) {
      await Promise.race([
        Promise.allSettled([...this.inFlight.values()]),
        new Promise((resolve) => setTimeout(resolve, 20)),
      ]);
    }
    const settled = this.inFlight.size === 0;
    // A build still running is still reading through this cache's database; dropping the folded
    // views it is using would only make that read slower, not safer. Clear it when it is ours again.
    if (settled) this.cache.clear();
    return settled;
  }

  /** For diagnostics at shutdown: which races were still being compared. */
  runningComparisons(): string[] {
    return [...this.inFlight.keys()];
  }

  private async build(groupId: string): Promise<CompareResult> {
    const { db } = this.deps;
    const group = repo.getGroup(db, groupId);
    if (!group) throw new Error(`unknown fan-out group ${groupId}`);
    const lanes = await Promise.all(
      repo.listGroupLanes(db, groupId).map(async (lane) => {
        // The candidate is what this lane executed, in the order it executed it. A replay or an
        // import in the lane is skipped entirely: it changed no file in this worktree.
        const attempts = repo.listExecutions(db, lane.id);
        const wt = repo.getWorktree(db, lane.id);
        const diff = wt
          ? await this.gate
              .run(() =>
                // The signal is applied here rather than folded into `gitOptions`, so that it is
                // this service's own cancellation that reaches git however the options are set.
                diffAgainstBase(wt.path, group.baseCommit, {
                  ...this.gitOptions,
                  signal: this.cancellation.signal,
                }),
              )
              .then(
                (d) => ({ files: d.files, patches: d.patches, error: undefined as string | undefined }),
                (err) => ({
                  files: [] as FileStat[],
                  patches: [] as Patch[],
                  error: describeGitFailure(err),
                }),
              )
          : { files: [] as FileStat[], patches: [] as Patch[], error: undefined };
        return this.laneCandidate(lane, attempts, wt?.path, wt?.branch, diff);
      }),
    );
    // A kept choice stays where the operator put it; what changes is whether the console still
    // claims it describes the worktree as it stands.
    const keptLane = lanes.find((l) => l.attempts.some((a) => a.runId === group.keptRunId));
    return {
      groupId,
      prompt: group.prompt,
      repoRoot: group.repoRoot,
      baseCommit: group.baseCommit,
      keptRunId: group.keptRunId,
      keptSuperseded: keptLane !== undefined && keptLane.runId !== group.keptRunId,
      lanes,
    };
  }

  private laneCandidate(
    lane: { id: string; name: string; groupIndex: number | null; cwd: string },
    attempts: RunRow[],
    worktreePath: string | undefined,
    branch: string | undefined,
    diff: { files: FileStat[]; patches: Patch[]; error?: string },
  ): CompareLane {
    const latest = attempts.at(-1);
    const summaries = attempts.map((run) => this.summaryOf(run));
    const totals = aggregateNumbers(attempts.map((r) => repo.lifecycleOf(r).numbers));
    return {
      laneId: lane.id,
      groupIndex: lane.groupIndex ?? 0,
      name: lane.name,
      runId: latest?.id ?? null,
      status: (latest?.status as RunStatus | undefined) ?? 'none',
      attempts: attempts.map(toAttempt),
      numbers: totals.numbers,
      // An attempt that reported nothing — still running, ended without a result event, or whose
      // result omitted a field — contributes nothing to that sum, which makes it a floor rather
      // than a total. The UI says so, and the "cheapest" marker skips such a lane entirely.
      metricGaps: totals.gaps,
      wallMs: totalWallMs(attempts),
      summary: aggregateSummaries(summaries),
      worktreePath: worktreePath ?? lane.cwd,
      branch: branch ?? '',
      files: diff.files,
      patches: diff.patches,
      error: diff.error,
    };
  }

  /** This run's event-derived summary, folding only what has arrived since the last comparison. */
  private summaryOf(run: RunRow): RunSummary {
    const { db } = this.deps;
    const cached = this.cache.get(run.id);
    const rows = repo.listEvents(db, run.id, cached?.lastSeq ?? 0);
    if (cached && rows.length === 0) {
      this.touch(run.id, cached);
      return cached.summary;
    }
    const base =
      cached?.view ??
      createRunView({
        runId: run.id,
        laneId: run.laneId,
        prompt: run.prompt,
        cwd: run.effectiveCwd,
        startedAt: run.startedAt,
        origin: run.origin,
      });
    let bytes = cached?.bytes ?? 0;
    // `Buffer.byteLength`, not `.length`: a JS string's length counts UTF-16 code units, so a line
    // of CJK text or emoji weighs roughly a third of what it actually costs — which is exactly the
    // recording most likely to be large. The budget is in bytes, so the weight must be too.
    for (const row of rows) bytes += Buffer.byteLength(row.json, 'utf8');
    const view = applyEnvelopes(
      base,
      rows.map((r) => repo.toEnvelope(r, run.laneId)),
    );
    const summary = summarizeRun(view);
    // A run too big for either budget is still compared correctly — the summary above is already
    // computed — it is simply folded again next time rather than held. Caching it would mean
    // evicting every other lane's work to keep one recording, or keeping one entry that breaks the
    // very bound the cache exists to respect.
    if (bytes > this.maxCacheableRunBytes || bytes > this.maxCachedBytes) this.cache.delete(run.id);
    else this.touch(run.id, { lastSeq: view.lastSeq, view, summary, bytes });
    return summary;
  }

  /**
   * Re-insert so the map's insertion order is least-recently-used first, then trim to the bounds.
   *
   * The bounds hold for every entry including the newest: an exception for "the one being used
   * right now" is how a cache with a stated budget ends up holding something larger than it. An
   * entry that cannot fit is not cached at all (see above), and its comparison is unaffected.
   */
  private touch(runId: string, entry: CachedRun): void {
    this.cache.delete(runId);
    this.cache.set(runId, entry);
    while (this.cache.size > this.maxCachedRuns || this.cachedBytes() > this.maxCachedBytes) {
      const oldest = this.cache.keys().next();
      if (oldest.done) break;
      this.cache.delete(oldest.value);
    }
  }
}

function toAttempt(run: RunRow): CandidateAttempt {
  return {
    runId: run.id,
    prompt: run.prompt,
    status: run.status as RunStatus,
    startedAt: run.startedAt,
    endedAt: run.endedAt ?? undefined,
    numbers: repo.lifecycleOf(run).numbers,
  };
}

/**
 * How long the agent worked, added up over the attempts — deliberately not `last.endedAt -
 * first.startedAt`, which would count the minutes the operator spent reading the first result
 * before typing the follow-up as though the agent had been running.
 */
function totalWallMs(attempts: RunRow[]): number | undefined {
  const spans = attempts
    .filter((r) => r.endedAt !== null)
    .map((r) => Math.max(0, (r.endedAt as number) - r.startedAt));
  return spans.length ? spans.reduce((a, b) => a + b, 0) : undefined;
}
