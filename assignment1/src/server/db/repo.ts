import { and, asc, desc, eq, gt, sql } from 'drizzle-orm';
import { parseLine } from '@/core/schemas';
import type { Envelope, RawEvent, RunLifecycle, RunStatus } from '@/core/types';
import type { CapturedSource, SourceOutcome, WrapUpDto } from '@/core/wrapup';
import type { Db } from './index';
import {
  type EventRow,
  events,
  fanoutGroups,
  type GroupRow,
  type LaneRow,
  lanes,
  type RunRow,
  runs,
  type WorktreeRow,
  type WrapUpFileRow,
  type WrapUpRow,
  type WrapUpRunRow,
  type WrapUpSourceRow,
  worktrees,
  wrapupFiles,
  wrapupRuns,
  wrapupSources,
  wrapups,
} from './schema';

// ---- lanes (sessions) ----
/**
 * `projectRoot` defaults to `cwd`: for an ordinary session the folder the operator chose *is* the
 * project, and only an isolated session — whose `cwd` is a checkout under the data directory —
 * has to say otherwise.
 */
export function createLane(
  db: Db,
  lane: Omit<LaneRow, 'archivedAt' | 'groupId' | 'groupIndex' | 'projectRoot'> &
    Partial<Pick<LaneRow, 'groupId' | 'groupIndex' | 'projectRoot'>>,
): LaneRow {
  const row: LaneRow = {
    archivedAt: null,
    groupId: null,
    groupIndex: null,
    ...lane,
    projectRoot: lane.projectRoot ?? lane.cwd,
  };
  db.insert(lanes).values(row).run();
  return row;
}

/**
 * Sessions, oldest first, optionally narrowed to one project and to the ones not archived.
 *
 * The project filter is here rather than at the call sites so every view — Home, the project rail,
 * the reconnect inventory — asks the same question of the same column. Membership is the recorded
 * `projectRoot` compared exactly; a path prefix would fold a monorepo's deliberately separate
 * subprojects back together.
 */
export function listLanes(db: Db, opts: { includeArchived?: boolean; projectRoot?: string } = {}): LaneRow[] {
  const rows = db.select().from(lanes).orderBy(asc(lanes.createdAt)).all();
  return rows.filter(
    (l) =>
      (opts.includeArchived === true || l.archivedAt === null) &&
      (opts.projectRoot === undefined || l.projectRoot === opts.projectRoot),
  );
}

/** Every project that has a session, most recently active first, with its counts. */
export function listProjects(db: Db): { root: string; sessions: number; lastActivityAt: number }[] {
  const byRoot = new Map<string, { root: string; sessions: number; lastActivityAt: number }>();
  for (const lane of listLanes(db, { includeArchived: true })) {
    const at = lastActivityAt(db, lane);
    const entry = byRoot.get(lane.projectRoot);
    if (entry) {
      entry.sessions += 1;
      entry.lastActivityAt = Math.max(entry.lastActivityAt, at);
    } else byRoot.set(lane.projectRoot, { root: lane.projectRoot, sessions: 1, lastActivityAt: at });
  }
  return [...byRoot.values()].sort((a, b) => b.lastActivityAt - a.lastActivityAt);
}

/**
 * When something last happened in this session: its newest run's end, or its start, or the
 * session's own creation. Recency is activity, not creation — a session answered an hour ago is
 * more current than one created yesterday and never used.
 */
export function lastActivityAt(db: Db, lane: LaneRow): number {
  const last = listRuns(db, lane.id).at(-1);
  return Math.max(lane.createdAt, last?.endedAt ?? last?.startedAt ?? 0);
}
export function getLane(db: Db, id: string): LaneRow | undefined {
  return db.select().from(lanes).where(eq(lanes.id, id)).get();
}
export function updateLane(db: Db, id: string, patch: Partial<Omit<LaneRow, 'id'>>): void {
  db.update(lanes).set(patch).where(eq(lanes.id, id)).run();
}
/**
 * Forget a session this server created and abandoned before anything ran in it.
 *
 * Deliberately the only deletion of a lane in the whole console, and not the general-purpose one it
 * looks like: a session with a run in it is history, and history is archived rather than removed
 * (`lanes.archive`). This exists for the rollback in `wrapups.create`, where the alternative is
 * leaving an operator with a session that was never started and cannot be explained.
 */
export function deleteLane(db: Db, id: string): void {
  db.delete(lanes).where(eq(lanes.id, id)).run();
}
export function listGroupLanes(db: Db, groupId: string): LaneRow[] {
  return db.select().from(lanes).where(eq(lanes.groupId, groupId)).orderBy(asc(lanes.groupIndex)).all();
}

// ---- runs ----
/**
 * `origin` is required rather than defaulted: every caller knows exactly what it is creating (the
 * process manager an execution, the replayer a replay or an import), and a default would let a new
 * call site create a run whose provenance nobody decided.
 */
export type NewRun = Pick<
  RunRow,
  'id' | 'laneId' | 'prompt' | 'effectiveCwd' | 'permission' | 'status' | 'startedAt' | 'origin'
> &
  Partial<
    Pick<
      RunRow,
      | 'browser'
      | 'groupId'
      | 'resumedFrom'
      | 'replayOf'
      | 'maxTurns'
      | 'model'
      | 'endedAt'
      | 'errorMessage'
      | 'sessionId'
      | 'costUsd'
      | 'durationMs'
      | 'durationApiMs'
      | 'numTurns'
    >
  >;

export function createRun(db: Db, run: NewRun): RunRow {
  const row: RunRow = {
    browser: 'off',
    groupId: null,
    sessionId: null,
    resumedFrom: null,
    replayOf: null,
    endedAt: null,
    exitCode: null,
    signal: null,
    stderrTail: null,
    errorMessage: null,
    costUsd: null,
    durationMs: null,
    durationApiMs: null,
    numTurns: null,
    model: null,
    maxTurns: null,
    ...run,
  };
  db.insert(runs).values(row).run();
  return row;
}
export function getRun(db: Db, id: string): RunRow | undefined {
  return db.select().from(runs).where(eq(runs.id, id)).get();
}
export function updateRun(db: Db, id: string, patch: Partial<Omit<RunRow, 'id'>>): void {
  db.update(runs).set(patch).where(eq(runs.id, id)).run();
}
export function listRuns(db: Db, laneId: string): RunRow[] {
  return db
    .select()
    .from(runs)
    .where(eq(runs.laneId, laneId))
    .orderBy(asc(runs.startedAt), asc(runs.id))
    .all();
}
/**
 * Did this lane actually run an agent for this row? Playback of a recording did not.
 *
 * Asked directly of the column, which is a closed domain: the Drizzle enum narrows what may be
 * written and the migration's `CHECK` stops anything else getting in. There used to be an
 * `originOf` here that folded any unrecognised string into `'execution'` — a guess dressed as a
 * conversion, and the most dangerous guess available, since `execution` is the value that makes a
 * run resumable and countable as a race candidate. Nothing coerces this value now; an unexpected
 * one simply is not an execution, which is the conservative direction.
 */
export function isExecution(run: RunRow): boolean {
  return run.origin === 'execution';
}

/**
 * Would a follow-up in this lane resume this run?
 *
 * Only executions qualify. A replay and an import both carry someone else's session: resuming them
 * hands `--resume` an id the CLI never issued, which is exactly what the QA run hit ("Provided
 * value "s-import-1" is not a UUID"). That used to be enforced by two different accidents —
 * `replay_of` being set for one, `session_id` being left null for the other — and now by the one
 * fact that says what the run is. A run that reported no turns (`num_turns: 0`, the failure above)
 * resumes an empty session, so it is not a candidate either; `null` turns mean the run has not
 * reported yet, which is not evidence against it.
 */
export function isResumeCandidate(run: RunRow): boolean {
  return isExecution(run) && run.sessionId !== null && (run.numTurns === null || run.numTurns > 0);
}

/** This lane's executions, oldest first — the runs that actually changed its directory. */
export function listExecutions(db: Db, laneId: string): RunRow[] {
  return listRuns(db, laneId).filter(isExecution);
}

export function latestSessionId(db: Db, laneId: string): string | undefined {
  // Newest first, `rowid` breaking ties: two runs started in the same millisecond order by
  // insertion. The predicate stays in `isResumeCandidate` so the router's DTO cannot disagree
  // with what a resume would actually pick.
  const rows = db
    .select()
    .from(runs)
    .where(eq(runs.laneId, laneId))
    .orderBy(desc(runs.startedAt), desc(sql`rowid`))
    .all();
  return rows.find(isResumeCandidate)?.sessionId ?? undefined;
}
export function failOrphanRuns(db: Db, now: number, message: string): RunRow[] {
  const orphans = db.select().from(runs).where(eq(runs.status, 'running')).all();
  for (const r of orphans) updateRun(db, r.id, { status: 'failed', endedAt: now, errorMessage: message });
  return orphans;
}

export function lifecycleOf(run: RunRow): RunLifecycle {
  // Whatever the run reported, field by field. Requiring all three to be present meant a result
  // that omitted one threw away the two it did report; carrying them separately keeps "unreported"
  // and "zero" distinguishable all the way to the display.
  const reported = [run.costUsd, run.durationMs, run.durationApiMs, run.numTurns];
  const numbers = reported.some((v) => v !== null)
    ? {
        costUsd: run.costUsd ?? undefined,
        durationMs: run.durationMs ?? undefined,
        durationApiMs: run.durationApiMs ?? undefined,
        numTurns: run.numTurns ?? undefined,
      }
    : undefined;
  const error = run.errorMessage
    ? {
        message: run.errorMessage,
        exitCode: run.exitCode,
        signal: run.signal,
        stderrTail: run.stderrTail ?? undefined,
      }
    : undefined;
  return {
    laneId: run.laneId,
    runId: run.id,
    status: run.status as RunStatus,
    origin: run.origin,
    startedAt: run.startedAt,
    endedAt: run.endedAt ?? undefined,
    sessionId: run.sessionId ?? undefined,
    numbers,
    error,
    resumable: isResumeCandidate(run),
  };
}

// ---- events ----
export function insertEvent(db: Db, row: EventRow): void {
  db.insert(events).values(row).run();
}
/**
 * A run's events after `afterSeq`, in order. `limit` makes a page: the hub backfills a reconnecting
 * client page by page rather than materializing a whole trajectory before it sends anything.
 */
export function listEvents(db: Db, runId: string, afterSeq = 0, limit?: number): EventRow[] {
  const q = db
    .select()
    .from(events)
    .where(and(eq(events.runId, runId), gt(events.seq, afterSeq)))
    .orderBy(asc(events.seq));
  return (limit === undefined ? q : q.limit(limit)).all();
}
/**
 * A stored line back as an event. Rows hold the agent's original text, which for a malformed line
 * is not JSON at all — so every reader goes through `parseLine` and gets the same `unparsed` event
 * the live stream produced, instead of a throw that would take out a whole backfill or replay.
 */
export function eventOf(row: Pick<EventRow, 'json'>): RawEvent {
  const parsed = parseLine(row.json);
  return parsed.ok ? parsed.event : { type: 'unparsed', raw: parsed.raw, error: parsed.error };
}

export function toEnvelope(row: EventRow, laneId: string): Envelope {
  return { laneId, runId: row.runId, seq: row.seq, receivedAt: row.receivedAt, event: eventOf(row) };
}

// ---- groups & worktrees ----
export function createGroup(db: Db, g: Omit<GroupRow, 'keptRunId'>): GroupRow {
  const row: GroupRow = { keptRunId: null, ...g };
  db.insert(fanoutGroups).values(row).run();
  return row;
}
export function getGroup(db: Db, id: string): GroupRow | undefined {
  return db.select().from(fanoutGroups).where(eq(fanoutGroups.id, id)).get();
}
export function listGroups(db: Db): GroupRow[] {
  return db.select().from(fanoutGroups).orderBy(desc(fanoutGroups.createdAt)).all();
}
export function setKeptRun(db: Db, groupId: string, runId: string | null): void {
  db.update(fanoutGroups).set({ keptRunId: runId }).where(eq(fanoutGroups.id, groupId)).run();
}
/**
 * `groupId` and `repoRoot` are spelled out rather than defaulted: a caller creating a worktree knows
 * whether a race asked for it and which repository it came from, and a default would let a new call
 * site record a checkout nobody can place.
 */
export type NewWorktree = Omit<WorktreeRow, 'groupId' | 'repoRoot'> &
  Pick<WorktreeRow, 'groupId' | 'repoRoot'>;

export function insertWorktree(db: Db, w: NewWorktree): void {
  db.insert(worktrees).values(w).run();
}
export function getWorktree(db: Db, laneId: string): WorktreeRow | undefined {
  return db.select().from(worktrees).where(eq(worktrees.laneId, laneId)).get();
}
/** Forget a worktree this server created but never handed to an agent (see `workspaces.create`). */
export function deleteWorktree(db: Db, laneId: string): void {
  db.delete(worktrees).where(eq(worktrees.laneId, laneId)).run();
}
/**
 * The repository a lane's worktree belongs to, or `null` when this lane does not work in one.
 *
 * Rows written since `0002_standalone_worktrees` carry the root themselves. Older ones were
 * backfilled from their race by that migration; the race is also consulted here so a row whose
 * backfill could not run (a database restored from a partial copy, a row inserted by an older
 * build) still resolves. When neither knows, the answer is `null` — the caller then has to ask git,
 * or say it cannot place this checkout, rather than guess.
 */
export function worktreeRepoRoot(db: Db, laneId: string): string | null {
  const w = getWorktree(db, laneId);
  if (!w) return null;
  if (w.repoRoot) return w.repoRoot;
  return (w.groupId ? getGroup(db, w.groupId)?.repoRoot : undefined) ?? null;
}

// ---- wrap-ups ----

/** Everything one capture recorded, written in a single transaction by `wrapups.create`. */
export interface NewWrapUp {
  wrapup: WrapUpRow;
  sources: WrapUpSourceRow[];
  runs: WrapUpRunRow[];
  files: WrapUpFileRow[];
}

/**
 * Record a captured package. Called inside the caller's transaction, so a failure anywhere in it
 * leaves no half-described wrap-up for the view to stumble over.
 */
export function insertWrapUp(db: Db, w: NewWrapUp): void {
  db.insert(wrapups).values(w.wrapup).run();
  for (const s of w.sources) db.insert(wrapupSources).values(s).run();
  for (const r of w.runs) db.insert(wrapupRuns).values(r).run();
  for (const f of w.files) db.insert(wrapupFiles).values(f).run();
}

/**
 * Forget a wrap-up this server created but never handed to an agent.
 *
 * The rollback half of `wrapups.create`, and the only thing that removes these rows: a wrap-up that
 * has run is history, and history is not deleted here any more than an archived session's is.
 */
export function deleteWrapUp(db: Db, laneId: string): void {
  db.delete(wrapupFiles).where(eq(wrapupFiles.wrapLaneId, laneId)).run();
  db.delete(wrapupRuns).where(eq(wrapupRuns.wrapLaneId, laneId)).run();
  db.delete(wrapupSources).where(eq(wrapupSources.wrapLaneId, laneId)).run();
  db.delete(wrapups).where(eq(wrapups.laneId, laneId)).run();
}

/**
 * What went into this session, or `undefined` when it is not a wrap-up.
 *
 * Reassembled from the rows rather than from the package on disk: the view has to be able to say
 * what the wrap-up was given even if the directory has since been moved or emptied, and the
 * manifest is the copy that travels with the files rather than the authority on what was recorded.
 */
export function getWrapUp(db: Db, laneId: string): WrapUpDto | undefined {
  const row = db.select().from(wrapups).where(eq(wrapups.laneId, laneId)).get();
  if (!row) return undefined;
  const sourceRows = db
    .select()
    .from(wrapupSources)
    .where(eq(wrapupSources.wrapLaneId, laneId))
    .orderBy(asc(wrapupSources.ordinal))
    .all();
  const runRows = db
    .select()
    .from(wrapupRuns)
    .where(eq(wrapupRuns.wrapLaneId, laneId))
    .orderBy(asc(wrapupRuns.ordinal))
    .all();
  const fileRows = db
    .select()
    .from(wrapupFiles)
    .where(eq(wrapupFiles.wrapLaneId, laneId))
    .orderBy(asc(wrapupFiles.storedPath))
    .all();
  const sources: CapturedSource[] = sourceRows.map((s) => ({
    laneId: s.sourceLaneId,
    ordinal: s.ordinal,
    name: s.name,
    outcome: s.outcome as SourceOutcome,
    revision: s.revision,
    runs: runRows
      .filter((r) => r.sourceLaneId === s.sourceLaneId)
      .map((r) => ({ runId: r.runId, status: r.status as RunStatus, prompt: r.prompt })),
    files: fileRows
      .filter((f) => f.sourceLaneId === s.sourceLaneId)
      .map((f) => ({ path: f.path, storedPath: f.storedPath, bytes: f.bytes, sha256: f.sha256 })),
    present: getLane(db, s.sourceLaneId) !== undefined,
  }));
  return {
    laneId: row.laneId,
    projectRoot: row.projectRoot,
    version: row.version,
    capturedAt: row.capturedAt,
    dir: row.dir,
    instructions: row.instructions,
    partial: row.partial !== 0,
    sources,
  };
}
