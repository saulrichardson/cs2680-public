import { type ChildProcess, spawn } from 'node:child_process';
import { statSync } from 'node:fs';
import { createInterface, type Interface } from 'node:readline';
import { parseLine } from '@/core/schemas';
import type { Envelope, RawEvent, RunError, RunLifecycle, RunNumbers, RunStatus } from '@/core/types';
import type { Db } from '@/server/db';
import * as repo from '@/server/db/repo';
import type { HubLike } from '@/server/ws/hub';
import { buildClaudeArgs, DEFAULT_ALLOWED_TOOLS, type StartRunInput, sanitizeEnv } from './args';
import { OWN_PROCESS_GROUP, type OwnedGroup, ownGroup, type ProcessControl } from './group';

export type { StartRunInput } from './args';
export type { ProcessControl } from './group';

interface LiveRun {
  child: ChildProcess;
  input: StartRunInput;
  startedAt: number;
  seq: number;
  /** The last `STDERR_TAIL_CHARS` characters the agent wrote, and no more (see `appendStderr`). */
  stderr: string;
  sawResult: boolean;
  resultIsError: boolean;
  resultErrors: string[];
  /** The result event landed before anyone asked to stop this run (see `outcomeStatus`). */
  resultBeforeCancel: boolean;
  numbers?: RunNumbers;
  sessionId?: string;
  cancelRequested: boolean;
  /**
   * The process group this run owns, when the platform has them and the spawn actually started. It
   * is led by the agent's own pid — but the *group* is what the manager owns, and a group outlives
   * its leader for as long as anything the agent started is still in it. `group.ts` owns every
   * question about identity, liveness and signalling, including the one this used to get wrong: a
   * spawn that never started has no identity and is never signalled.
   */
  group: OwnedGroup;
  /** Escalation timers, cleared once the group is gone. */
  timers: NodeJS.Timeout[];
  /** Polls for the group's death after the leader has exited; see `releaseWhenReaped`. */
  watchTimer: NodeJS.Timeout | null;
  /** The line reader, detached and closed the moment the run's outcome is decided. */
  rl: Interface | null;
  /**
   * The run's outcome has been *decided* and published: its status, numbers and error are final and
   * nothing may append events to it or decide a second outcome.
   *
   * Deliberately not "written": deciding includes the case where writing the row failed (the
   * database is exactly what has broken in the fatal-persistence path), and the decision stands
   * either way. The process it belongs to may also still be very much alive.
   */
  outcomeDecided: boolean;
  /** A reaping is under way: the escalation timers are armed and must not be armed again. */
  reaping: boolean;
  /**
   * Why this run's processes could not be confirmed gone. Set when the escalation has run its
   * course and something is still there — a process stuck in the kernel, or one we are not
   * permitted to signal. The run stays owned, so its lane stays blocked, and this says why.
   */
  cleanupError: string | null;
}

/**
 * A run whose processes shutdown could not confirm gone.
 *
 * Returned by `shutdown` rather than only logged, because the server's exit status and its
 * `ShutdownReport` are the only places an operator learns that agents may have outlived the
 * console. `pgid` is the group that was being signalled; `undefined` means the platform has no
 * process groups, or the spawn never got an identity — in which case there was nothing to confirm.
 */
export interface UnconfirmedCleanup {
  runId: string;
  pgid: number | undefined;
}

/** What `shutdown` was able to confirm about the processes this manager owned. */
export interface ProcessShutdownResult {
  /** Empty when every owned process group was confirmed gone. */
  unconfirmed: UnconfirmedCleanup[];
}

/** Raised by `start` once `shutdown` has begun. Nothing new may be spawned after that point. */
export class ShuttingDownError extends Error {
  constructor() {
    super('the server is shutting down');
    this.name = 'ShuttingDownError';
  }
}

/** How often a reaped group is checked for having finally gone. */
const REAP_POLL_MS = 25;

/**
 * How much of the agent's stderr is kept for the inspector. A character is at most four bytes, so
 * the tail costs at most ~16 KB per live run however much the agent writes.
 */
const STDERR_TAIL_CHARS = 4000;

/**
 * The terminal status a dead child means.
 *
 * A clean result (`subtype: success`, `is_error: false`) plus exit 0 is a finished run even if a
 * Stop was requested — provided the result had already arrived when the Stop came in. That is the
 * race the QA run hit: the agent wraps up on its own while the user is reaching for Stop, and
 * reporting `cancelled` then throws away a run that actually completed. A Stop that comes *first*
 * still cancels, whatever the interrupted turn writes on its way out (the real CLI answers SIGINT
 * with an `error_during_execution` result; a friendlier CLI might answer with a clean one).
 */
export function outcomeStatus(o: {
  sawResult: boolean;
  resultIsError: boolean;
  resultBeforeCancel: boolean;
  cancelRequested: boolean;
  code: number | null;
}): RunStatus {
  const clean = o.sawResult && !o.resultIsError && o.code === 0;
  if (clean && (!o.cancelRequested || o.resultBeforeCancel)) return 'finished';
  if (o.cancelRequested) return 'cancelled';
  return 'failed';
}

/**
 * Owns the agent subprocesses.
 *
 * Two things a run needs are deliberately kept apart, because collapsing them is what let a child
 * survive its own run (readiness review R4):
 *
 * - **its outcome** — the status, numbers and error written to the row and published to clients.
 *   Decided once, by `settle`, and never revised.
 * - **its processes** — signalled by `reap` until they are really gone, and only then forgotten.
 *
 * What is owned is the *process group*, not the agent. The agent runs the operator's tools as its
 * own children in that group, and the group outlives its leader: a real CLI ends its turn on SIGINT
 * promptly while the `pytest` it started does not. Releasing the run when the leader exited, and
 * refusing to signal once it had, is how a stubborn tool survived cancellation, shutdown and the
 * server itself.
 *
 * A run that ends *on its own* is the other case, and the manager deliberately does not reach into
 * it: whatever the agent chose to leave running — a dev server the operator asked for — is the
 * operator's, and the run is over when the agent is. Only a reaping (a Stop, a fatal persistence
 * failure, a shutdown) follows the group down.
 *
 * Every way a run can end goes through both halves, in that order, and every one of them is
 * idempotent.
 */
export class ProcessManager {
  private readonly live = new Map<string, LiveRun>();
  private readonly claudeBin: string;
  private readonly allowedTools: string;
  private readonly now: () => number;
  private readonly cancelGraceMs: number;
  private readonly control: ProcessControl | undefined;
  /** Set once `shutdown` starts; every later `shutdown` is the same one, and `start` is refused. */
  private closing: Promise<ProcessShutdownResult> | null = null;
  /**
   * What shutdown could not confirm, kept after the live runs are dropped.
   *
   * Ownership at that point is *diagnostic only*: the server is leaving, so holding the escalation
   * timers open would keep a process (and, in a test, the event loop) alive for ever with nothing
   * left to escalate to. The identities stay so `cleanupErrorOf` and the shutdown report can still
   * say which agents may have outlived the console.
   */
  private readonly unconfirmed = new Map<string, UnconfirmedCleanup>();

  constructor(
    private readonly deps: {
      db: Db;
      hub: HubLike;
      claudeBin?: string;
      allowedTools?: string;
      now?: () => number;
      cancelGraceMs?: number;
      control?: ProcessControl;
    },
  ) {
    this.control = deps.control;
    this.claudeBin = deps.claudeBin ?? process.env.MISSION_CONTROL_CLAUDE_BIN ?? 'claude';
    this.allowedTools =
      deps.allowedTools ?? process.env.MISSION_CONTROL_ALLOWED_TOOLS ?? DEFAULT_ALLOWED_TOOLS;
    this.now = deps.now ?? (() => Date.now());
    this.cancelGraceMs = deps.cancelGraceMs ?? 5000;
  }

  /**
   * Is a process for this run still alive?
   *
   * This is the only question about a run's process, and the lane guard's answer to "is this lane
   * busy?". There used to be a second one — "has it printed its result yet?" — which freed the lane
   * while the child was still running, on the reasoning that it would not touch the directory
   * again. It is not a promise anything enforces, and it let a second agent into a directory the
   * first still holds. The client does not need the early release: a run's status comes only from
   * the lifecycle published when the child exits, so the composer and this guard change together.
   */
  isLive(runId: string): boolean {
    return this.live.has(runId);
  }

  /** The OS process behind a live run, for tests and diagnostics. */
  pidOf(runId: string): number | undefined {
    return this.live.get(runId)?.child.pid;
  }

  async start(input: StartRunInput): Promise<void> {
    // Admission closes before anything else does. A fan-out request that was still awaiting `git
    // worktree add` when the signal arrived would otherwise spawn its agent *after* `shutdown` took
    // its snapshot of live runs, and nothing would ever reap it.
    if (this.closing) throw new ShuttingDownError();
    const startedAt = this.now();
    repo.createRun(this.deps.db, {
      id: input.runId,
      laneId: input.laneId,
      prompt: input.prompt,
      effectiveCwd: input.cwd,
      permission: input.permission,
      // Every execution is launched with the browser (`buildClaudeArgs`), so every execution
      // records that it was. The column stays because old rows recorded something else and their
      // trajectories must keep reading truthfully.
      browser: 'chrome',
      // The process manager is the only thing that spawns an agent, so it is the only thing that
      // creates an execution.
      origin: 'execution',
      status: 'running',
      startedAt,
      groupId: input.groupId,
      resumedFrom: input.resumedFrom,
      maxTurns: input.maxTurns,
      model: input.model,
    });

    if (!isDirectory(input.cwd)) {
      this.recordOutcome(input, startedAt, 'failed', {
        message: `${input.cwd} is not a directory`,
        exitCode: null,
      });
      return;
    }

    const args = buildClaudeArgs(input, this.allowedTools);
    let child: ChildProcess;
    try {
      child = spawn(this.claudeBin, args, {
        cwd: input.cwd,
        env: sanitizeEnv(process.env),
        stdio: ['ignore', 'pipe', 'pipe'],
        // The agent runs the operator's tools as its own children. Giving it its own process group
        // means a Stop reaches those too, instead of leaving a `pytest` or a dev server holding the
        // directory the next run is about to use. POSIX only; see `signal`.
        detached: OWN_PROCESS_GROUP,
      });
    } catch (err) {
      this.recordOutcome(input, startedAt, 'failed', {
        message: err instanceof Error ? err.message : String(err),
        exitCode: null,
      });
      return;
    }

    const state: LiveRun = {
      child,
      input,
      startedAt,
      seq: 0,
      stderr: '',
      sawResult: false,
      resultIsError: false,
      resultErrors: [],
      resultBeforeCancel: false,
      cancelRequested: false,
      // The agent leads its own group, so its pid names the group — once it has one. `ownGroup`
      // reads that every time and refuses to signal a spawn that never started.
      group: ownGroup(child, this.control),
      timers: [],
      watchTimer: null,
      rl: null,
      outcomeDecided: false,
      reaping: false,
      cleanupError: null,
    };
    this.live.set(input.runId, state);
    this.deps.hub.publishLifecycle({
      laneId: input.laneId,
      runId: input.runId,
      origin: 'execution',
      status: 'running',
      startedAt,
    });

    child.on('error', (err) => {
      // spawn failures (ENOENT) arrive here; 'close' may or may not follow
      this.settle(state, 'failed', {
        message: `could not start ${this.claudeBin}: ${err.message}`,
        exitCode: null,
      });
      if (child.pid === undefined) this.forget(state);
    });

    if (child.stdout) {
      state.rl = createInterface({ input: child.stdout, crlfDelay: Number.POSITIVE_INFINITY });
      state.rl.on('line', (line) => this.onLine(state, line));
    }
    // Decode once, here, rather than per chunk: a multi-byte character split across two chunks
    // would otherwise be mangled by the two independent `toString()` calls.
    child.stderr?.setEncoding('utf8');
    child.stderr?.on('data', (chunk: string) => appendStderr(state, chunk));

    child.on('close', (code, signal) => {
      // The outcome may already be decided (a Stop's fatal error, a shutdown). Whatever it says
      // stands: the process ending is not new information about why the run ended.
      if (!state.outcomeDecided) {
        let error: RunError | undefined;
        const status = outcomeStatus({
          sawResult: state.sawResult,
          resultIsError: state.resultIsError,
          resultBeforeCancel: state.resultBeforeCancel,
          cancelRequested: state.cancelRequested,
          code,
        });
        // `stderrTail` reaches the row for every outcome (below), cancelled included — the inspector
        // needs the agent's last words even when the run has no error message of its own.
        if (status === 'failed') {
          const why = state.resultErrors.length
            ? state.resultErrors.join('; ')
            : state.sawResult && state.resultIsError
              ? 'the agent reported an error result'
              : code === 0
                ? 'the agent exited without a result event'
                : `the agent exited with code ${code ?? 'null'}${signal ? ` (${signal})` : ''}`;
          error = { message: why, exitCode: code, signal, stderrTail: state.stderr || undefined };
        }
        this.settle(state, status, error, {
          exitCode: code,
          signal: signal ?? null,
        });
      }
      this.releaseWhenReaped(state);
    });
  }

  /**
   * Stop this run. Answers whether there was anything to stop — a second Stop while the first is
   * still escalating is the same request, and says so, rather than arming a second set of timers.
   */
  async cancel(runId: string): Promise<boolean> {
    const state = this.live.get(runId);
    if (!state) return false;
    state.cancelRequested = true;
    this.reap(state);
    return true;
  }

  /**
   * End a run because the *server* could not go on with it — a race whose other lanes failed to
   * start, for instance. The run reads as failed with the reason given, because nobody pressed
   * anything: reporting these as "Stopped by you" attributes the server's decision to the operator.
   */
  async abort(runId: string, reason: string): Promise<boolean> {
    const state = this.live.get(runId);
    if (!state) return false;
    this.settle(state, 'failed', { message: reason, exitCode: null });
    this.reap(state);
    return true;
  }

  /**
   * Shutting down twice is the same shutdown; every caller waits on the one that is running.
   *
   * The result is what the caller reports: an empty `unconfirmed` means every agent this manager
   * owned is provably gone. Anything in it is a process that may still be running with nobody left
   * to stop it, and `context.close` turns that into a failed shutdown rather than a quiet exit.
   */
  shutdown(): Promise<ProcessShutdownResult> {
    this.closing ??= this.runShutdown();
    return this.closing;
  }

  private async runShutdown(): Promise<ProcessShutdownResult> {
    const states = [...this.live.values()];
    // The server is going away, so no `close` handler will get to write these runs' outcomes (and
    // for a child that has to be killed there is no clean outcome to write). Record and publish the
    // terminal state first — otherwise the run stays `running` in the database until the next boot's
    // `failOrphanRuns` sweeps it, and any client watching sees a run that never ends.
    for (const state of states) {
      this.settle(state, 'failed', { message: 'server shutting down', exitCode: null });
      this.reap(state);
    }
    // Wait for the escalation to finish its work rather than returning while processes are still
    // up: this process is about to exit, and anything alive in one of these groups then is orphaned
    // for good. The wait watches the *groups*, so a tool that outlives its agent still counts.
    const deadline = Date.now() + this.cancelGraceMs * 3;
    while (states.some((s) => s.group.alive()) && Date.now() < deadline)
      await new Promise((r) => setTimeout(r, 20));
    for (const s of states) {
      if (s.group.alive()) s.group.signal('SIGKILL');
      this.forget(s);
    }
    // Give that last SIGKILL a moment to land before the server disappears.
    if (states.some((s) => s.group.alive())) await new Promise((r) => setTimeout(r, REAP_POLL_MS * 2));
    // What is left is what this shutdown could not clean up. It is *reported*, not swallowed: the
    // ownership that kept a lane blocked is over — the server is leaving — but the caller still
    // has to be able to say that agents may have outlived it, and exit non-zero for saying so.
    for (const s of states) {
      if (!s.group.alive()) continue;
      this.unconfirmed.set(s.input.runId, { runId: s.input.runId, pgid: s.group.pgid });
      console.warn(`run ${s.input.runId}: process group ${s.group.pgid} outlived shutdown`);
    }
    // A `close` event for a child killed on the way out may never be delivered before the process
    // exits, so drop everything explicitly rather than relying on it. `forget` above has already
    // cleared each run's timers, so nothing is left armed against a database that is closing.
    this.live.clear();
    return { unconfirmed: [...this.unconfirmed.values()] };
  }

  private onLine(state: LiveRun, line: string): void {
    // A line that arrives after the run's outcome is written (a child still flushing stdout while
    // it is being killed, or after shutdown) must not append events or revive the run.
    if (state.outcomeDecided || line.trim().length === 0) return;
    const parsed = parseLine(line);
    const event: RawEvent = parsed.ok
      ? parsed.event
      : { type: 'unparsed', raw: parsed.raw, error: parsed.error };
    state.seq += 1;
    const receivedAt = this.now();
    const env: Envelope = {
      laneId: state.input.laneId,
      runId: state.input.runId,
      seq: state.seq,
      receivedAt,
      event,
    };
    // Persisting the line is what makes it real: the database is the copy every reconnect,
    // replay and export reads. If that fails (a full disk, a locked or corrupt file) there is no
    // honest way to keep streaming — the client would see events that no backfill can reproduce —
    // so the run ends visibly, with the reason, and the child is reaped.
    try {
      repo.insertEvent(this.deps.db, {
        runId: env.runId,
        seq: env.seq,
        receivedAt,
        type: event.type,
        parentToolUseId: typeof event.parent_tool_use_id === 'string' ? event.parent_tool_use_id : null,
        // Always the agent's own line, even when it did not parse: `/api/export` hands back exactly
        // what the CLI wrote, and the `unparsed` shape is re-derived on read (see `repo.eventOf`).
        // The row's `type` column still says 'unparsed', so such rows stay findable.
        json: line,
      });
      if (event.type === 'system' && event.subtype === 'init') {
        // Only what the event actually carries. An init line with neither a model nor a session id
        // is unusual but not broken — and asking the ORM to set no columns at all threw "No values
        // to set", which `onLine` then reported as a fatal persistence failure and reaped a healthy
        // agent over. A missing optional field is not a disk failure.
        const patch: { model?: string; sessionId?: string } = {};
        if (typeof event.model === 'string') patch.model = event.model;
        if (typeof event.session_id === 'string') patch.sessionId = event.session_id;
        if (Object.keys(patch).length > 0) repo.updateRun(this.deps.db, env.runId, patch);
        if (patch.sessionId) state.sessionId = patch.sessionId;
      }
      if (event.type === 'result') {
        state.sawResult = true;
        state.resultBeforeCancel = !state.cancelRequested;
        state.resultIsError = event.is_error === true || event.subtype !== 'success';
        state.resultErrors = Array.isArray(event.errors)
          ? event.errors.filter((x): x is string => typeof x === 'string')
          : [];
        // Only what the result reported. `?? 0` here turned an omitted `total_cost_usd` into a
        // free run, in the row as well as in the view, so no later aggregation could tell the two
        // apart. A reported zero is still stored as zero.
        state.numbers = {
          costUsd: finiteNumber(event.total_cost_usd),
          durationMs: finiteNumber(event.duration_ms),
          durationApiMs: finiteNumber(event.duration_api_ms),
          numTurns: finiteNumber(event.num_turns),
        };
        if (typeof event.session_id === 'string') state.sessionId = event.session_id;
        repo.updateRun(this.deps.db, env.runId, {
          costUsd: state.numbers.costUsd ?? null,
          durationMs: state.numbers.durationMs ?? null,
          durationApiMs: state.numbers.durationApiMs ?? null,
          numTurns: state.numbers.numTurns ?? null,
          sessionId: state.sessionId ?? null,
        });
      }
      this.deps.hub.publishEvent(env);
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      this.settle(state, 'failed', { message: `could not persist events: ${why}`, exitCode: null });
      // Nothing is reading this child any more, so it must not be left running (and billing) — and
      // it keeps being escalated until it is gone, however it answers the first signal.
      this.reap(state);
    }
  }

  /**
   * Decide this run's outcome, once. Writes the row, publishes the lifecycle, and stops reading the
   * child's output. It does *not* stop owning the process — `reap` does that, and `forget` ends it.
   */
  private settle(
    state: LiveRun,
    status: RunStatus,
    error?: RunError,
    extra: { exitCode?: number | null; signal?: string | null } = {},
  ): void {
    if (state.outcomeDecided) return;
    state.outcomeDecided = true;
    state.rl?.removeAllListeners('line');
    state.rl?.close();
    state.rl = null;
    this.recordOutcome(state.input, state.startedAt, status, error, {
      numbers: state.numbers,
      sessionId: state.sessionId,
      exitCode: extra.exitCode,
      signal: extra.signal,
      stderrTail: state.stderr || undefined,
    });
  }

  /** Write and publish a run's terminal state. Also used for runs that never got a process. */
  private recordOutcome(
    input: StartRunInput,
    startedAt: number,
    status: RunStatus,
    error?: RunError,
    extra: {
      numbers?: RunNumbers;
      sessionId?: string;
      exitCode?: number | null;
      signal?: string | null;
      stderrTail?: string;
    } = {},
  ): void {
    const endedAt = this.now();
    let row: ReturnType<typeof repo.getRun>;
    try {
      repo.updateRun(this.deps.db, input.runId, {
        status,
        endedAt,
        errorMessage: error?.message ?? null,
        exitCode: extra.exitCode ?? error?.exitCode ?? null,
        signal: extra.signal ?? null,
        stderrTail: extra.stderrTail ?? null,
        sessionId: extra.sessionId ?? undefined,
      });
      row = repo.getRun(this.deps.db, input.runId);
    } catch (err) {
      // The database is exactly what has just failed in the persistence case, and losing the run's
      // last word on top of that would leave every client showing it as running for ever. Say what
      // happened from what is known in memory, and let the caller get on with reaping the process.
      console.error(`could not record the outcome of run ${input.runId}`, err);
      row = undefined;
    }
    const lc: RunLifecycle = row
      ? repo.lifecycleOf(row)
      : {
          laneId: input.laneId,
          runId: input.runId,
          origin: 'execution',
          status,
          startedAt,
          endedAt,
          error,
        };
    this.deps.hub.publishLifecycle({
      ...lc,
      status,
      error: error ?? lc.error,
      numbers: extra.numbers ?? lc.numbers,
      sessionId: extra.sessionId ?? lc.sessionId,
    });
  }

  /**
   * Make sure the run's processes actually end: SIGINT now, SIGTERM after the grace period, SIGKILL
   * after twice that. SIGINT first because it lets the CLI end its turn cleanly and still report its
   * numbers. Arming this twice would only mean more signals at the same times, so a run already
   * being reaped is left alone — and a reaping that has begun is never called off, whatever the
   * leader does next.
   */
  private reap(state: LiveRun): void {
    if (state.reaping) return;
    state.reaping = true;
    // Nothing left to signal — including the spawn that never started, which has no identity to
    // signal and must never be given one by default. Releasing the run is *not* this method's
    // business: the leader's `close` handler may not have run yet, and with it the last lines of
    // stdout the agent wrote on its way out. Letting that handler finish is what keeps a result
    // event from being thrown away by the cleanup that follows it.
    if (!state.group.alive()) return;
    state.group.signal('SIGINT');
    state.timers.push(setTimeout(() => state.group.signal('SIGTERM'), this.cancelGraceMs));
    state.timers.push(setTimeout(() => state.group.signal('SIGKILL'), this.cancelGraceMs * 2));
    this.watchGroup(state);
  }

  /**
   * What to do when the agent itself has exited.
   *
   * If nobody asked for this run to stop, it is simply over: anything the agent deliberately left
   * running belongs to the operator, and the lane is free. If a reaping is under way, the run stays
   * owned — lane still busy, escalation still armed — until the group it created is gone too.
   */
  private releaseWhenReaped(state: LiveRun): void {
    if (!state.reaping || !state.group.alive()) {
      this.forget(state);
      return;
    }
    this.watchGroup(state);
  }

  /**
   * Hold the run until its process group is gone, then release it.
   *
   * The escalation is bounded; this watch is not, and deliberately so. When SIGKILL has been sent
   * and something is *still* there — stuck in uninterruptible I/O, or a process we are not
   * permitted to signal — the honest state is "this directory is still occupied and I could not
   * clean it up", not "the lane is free". Releasing it on a timer would admit a second agent into a
   * directory the first is still holding, which is the admission failure this whole model exists to
   * prevent. So the deadline records *why* cleanup could not be confirmed (surfaced by
   * `cleanupErrorOf`, and reported by the lane guard), keeps the lane blocked, and keeps watching:
   * if the process does eventually go, the lane frees itself.
   *
   * Shutdown is the one place that gives up, because the server is leaving anyway; it says so.
   */
  private watchGroup(state: LiveRun): void {
    if (state.watchTimer) return;
    const deadline = Date.now() + this.cancelGraceMs * 3 + REAP_POLL_MS * 4;
    const tick = () => {
      state.watchTimer = null;
      if (!this.live.has(state.input.runId)) return;
      if (!state.group.alive()) {
        this.forget(state);
        return;
      }
      if (state.cleanupError === null && Date.now() >= deadline) {
        state.cleanupError = `could not confirm that everything run ${state.input.runId} started has exited (process group ${state.group.pgid}); this lane stays blocked until it has`;
        console.warn(`hub: ${state.cleanupError}`);
      }
      state.watchTimer = setTimeout(tick, REAP_POLL_MS);
    };
    state.watchTimer = setTimeout(tick, REAP_POLL_MS);
  }

  /**
   * Why this run's lane is still blocked after its outcome, when the reason is that cleanup could
   * not be confirmed. `undefined` while a reaping is simply still in progress.
   *
   * It keeps answering for a run shutdown gave up on. The lane is not blocked then — nothing is,
   * the server is going — but the reason is still the truth about that run, and losing it with the
   * live entry would make the shutdown report the only place it was ever said.
   */
  cleanupErrorOf(runId: string): string | undefined {
    const live = this.live.get(runId)?.cleanupError;
    if (live) return live;
    const abandoned = this.unconfirmed.get(runId);
    return abandoned
      ? `could not confirm that everything run ${runId} started has exited (process group ${abandoned.pgid}); the server stopped waiting because it was shutting down`
      : undefined;
  }

  /** Stop owning the run: everything it started has exited (or it never started anything). */
  private forget(state: LiveRun): void {
    for (const t of state.timers) clearTimeout(t);
    state.timers = [];
    if (state.watchTimer) clearTimeout(state.watchTimer);
    state.watchTimer = null;
    state.rl?.removeAllListeners('line');
    state.rl?.close();
    state.rl = null;
    this.live.delete(state.input.runId);
  }
}

/**
 * Keep the last `STDERR_TAIL_CHARS` characters, whatever shape the writes arrive in. The previous
 * version dropped whole chunks off the front and stopped once one chunk was left, so a single
 * oversized write was kept in full.
 */
function appendStderr(state: LiveRun, chunk: string): void {
  state.stderr =
    chunk.length >= STDERR_TAIL_CHARS
      ? chunk.slice(-STDERR_TAIL_CHARS)
      : (state.stderr + chunk).slice(-STDERR_TAIL_CHARS);
}

function isDirectory(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

/** A reported number, or `undefined` when the event did not carry a usable one. */
function finiteNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}
