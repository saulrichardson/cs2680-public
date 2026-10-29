import { index, integer, primaryKey, real, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const lanes = sqliteTable('lanes', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  /** Where this session's agent actually runs. For an isolated session, inside its own checkout. */
  cwd: text('cwd').notNull(),
  /**
   * The project this session belongs to: the folder the operator chose.
   *
   * Separate from `cwd` because they answer different questions. `cwd` is where files are written,
   * and for an isolated session that is a checkout under the console's data directory — nothing an
   * operator would recognise. `project_root` is what groups related work, so a session and the
   * isolated checkout it spawned stay together in one list.
   *
   * A chosen subdirectory of a monorepo is a project. Nothing here widens a selection to its git
   * root, and nothing groups by name or by path prefix: membership is this recorded value,
   * canonicalised once when the session is created.
   */
  projectRoot: text('project_root').notNull().default(''),
  permission: text('permission').notNull(),
  groupId: text('group_id'),
  groupIndex: integer('group_index'),
  createdAt: integer('created_at').notNull(),
  /**
   * When the operator archived this session, or `null` while it is active.
   *
   * Archiving hides a session from the project's active list. It keeps every run, every event and
   * every file, and `reopen` undoes it. It is not a way to stop work: the server refuses to
   * archive a session it still owns execution or playback for.
   */
  archivedAt: integer('archived_at'),
});

export const runs = sqliteTable(
  'runs',
  {
    id: text('id').primaryKey(),
    laneId: text('lane_id').notNull(),
    groupId: text('group_id'),
    prompt: text('prompt').notNull(),
    effectiveCwd: text('effective_cwd').notNull(),
    permission: text('permission').notNull(),
    sessionId: text('session_id'),
    resumedFrom: text('resumed_from'),
    replayOf: text('replay_of'),
    /**
     * What made this row: an `execution` (a `claude` child process that did work in
     * `effective_cwd`), a `replay` of a stored run, or an `import` of a recording from elsewhere.
     *
     * Before this column the three were told apart by guesswork — `replay_of` for a replay, a null
     * `session_id` plus a generated prompt for an import — and the guesses disagreed with each
     * other: an import could take a lane's Stop away from the agent still working in it, and a
     * replay of a kept race result replaced that result in Compare (readiness review R2, R3).
     * Everything that asks "did this run actually do the work?" now asks this column.
     *
     * A closed domain in both directions: the enum narrows `RunRow['origin']` so no caller can
     * write an arbitrary string, and `0001_add_run_origin` carries a matching `CHECK` so no other
     * writer can either. The CHECK lives in the migration rather than here on purpose — SQLite can
     * add a column-level CHECK in an `ALTER`, which keeps that migration additive, while declaring
     * it in this schema would make drizzle-kit rebuild the table.
     */
    origin: text('origin', { enum: ['execution', 'replay', 'import'] })
      .notNull()
      .default('execution'),
    /** What this run was started with, so a trajectory can be read years later (see `lanes.browser`). */
    browser: text('browser', { enum: ['off', 'chrome'] })
      .notNull()
      .default('off'),
    status: text('status').notNull(),
    startedAt: integer('started_at').notNull(),
    endedAt: integer('ended_at'),
    exitCode: integer('exit_code'),
    signal: text('signal'),
    stderrTail: text('stderr_tail'),
    errorMessage: text('error_message'),
    costUsd: real('cost_usd'),
    durationMs: integer('duration_ms'),
    durationApiMs: integer('duration_api_ms'),
    numTurns: integer('num_turns'),
    model: text('model'),
    maxTurns: integer('max_turns'),
  },
  (t) => [index('runs_lane_started_idx').on(t.laneId, t.startedAt), index('runs_group_idx').on(t.groupId)],
);

export const events = sqliteTable(
  'events',
  {
    runId: text('run_id').notNull(),
    seq: integer('seq').notNull(),
    receivedAt: integer('received_at').notNull(),
    type: text('type').notNull(),
    parentToolUseId: text('parent_tool_use_id'),
    json: text('json').notNull(),
  },
  (t) => [primaryKey({ columns: [t.runId, t.seq] })],
);

export const fanoutGroups = sqliteTable('fanout_groups', {
  id: text('id').primaryKey(),
  prompt: text('prompt').notNull(),
  repoRoot: text('repo_root').notNull(),
  baseCommit: text('base_commit').notNull(),
  createdAt: integer('created_at').notNull(),
  keptRunId: text('kept_run_id'),
});

/**
 * A git worktree this server created and a lane works in. One row per lane.
 *
 * `group_id` is nullable because a worktree is not a race. Racing was simply the first thing that
 * needed one, and while the column was `NOT NULL` the only way to give a single session an isolated
 * checkout was to invent a one-member race — a group in Compare, a "Race" pill in the header, and a
 * lane the keep/supersede rules would reason about as a candidate. Membership of a race is now what
 * it always meant: optional, and set only when a race really created this worktree.
 *
 * `repo_root` is the repository the worktree belongs to, recorded rather than re-derived. It is what
 * answers "another session in the same project" from inside a lane whose own cwd is a linked
 * checkout, and what `git worktree remove` has to be run from. It is nullable only for rows written
 * before `0002_standalone_worktrees`, which backfills every one whose race still exists; a row whose
 * race is gone has no recorded root and says so rather than claiming one (`worktreeRepoRoot`).
 */
export const worktrees = sqliteTable('worktrees', {
  laneId: text('lane_id').primaryKey(),
  groupId: text('group_id'),
  repoRoot: text('repo_root'),
  path: text('path').notNull(),
  branch: text('branch').notNull(),
  baseCommit: text('base_commit').notNull(),
});

/**
 * A session that was given a package of other sessions' work to start from. One row per wrap-up.
 *
 * The lane is an ordinary session; everything that makes it a wrap-up is here rather than inferred
 * from its name, prompt or directory. `dir` is both the package directory and the lane's working
 * folder, under the console's data directory — never a source session's folder.
 */
export const wrapups = sqliteTable('wrapups', {
  laneId: text('lane_id').primaryKey(),
  /** The project every source belonged to, and the project this session is grouped with. */
  projectRoot: text('project_root').notNull(),
  /** The package format (`WRAPUP_VERSION`), so a later reader knows what shape it is holding. */
  version: integer('version').notNull(),
  capturedAt: integer('captured_at').notNull(),
  dir: text('dir').notNull(),
  /** What the operator asked the wrap-up to do. The sources' own words are never instructions. */
  instructions: text('instructions').notNull(),
  /** A source that had not finished was included, with the operator's explicit acknowledgement. */
  partial: integer('partial').notNull(),
});

/**
 * One session a package was captured from, as it stood at capture.
 *
 * `name` and `outcome` are recorded rather than re-read: the question is what the wrap-up was
 * *given*, and a source renamed or continued since has not changed that. `revision` is the token
 * the capture was validated against (`sourceRevision`).
 */
export const wrapupSources = sqliteTable(
  'wrapup_sources',
  {
    wrapLaneId: text('wrap_lane_id').notNull(),
    sourceLaneId: text('source_lane_id').notNull(),
    ordinal: integer('ordinal').notNull(),
    name: text('name').notNull(),
    outcome: text('outcome').notNull(),
    revision: text('revision').notNull(),
  },
  (t) => [
    primaryKey({ columns: [t.wrapLaneId, t.sourceLaneId] }),
    index('wrapup_sources_source_idx').on(t.sourceLaneId),
  ],
);

/** One execution of a source session that the package covers, with its status and prompt then. */
export const wrapupRuns = sqliteTable(
  'wrapup_runs',
  {
    wrapLaneId: text('wrap_lane_id').notNull(),
    sourceLaneId: text('source_lane_id').notNull(),
    runId: text('run_id').notNull(),
    ordinal: integer('ordinal').notNull(),
    status: text('status').notNull(),
    prompt: text('prompt').notNull(),
  },
  (t) => [primaryKey({ columns: [t.wrapLaneId, t.runId] })],
);

/**
 * One file copied into a package, and the bytes it was copied from.
 *
 * `sha256` is provenance — which bytes went in — and deliberately not a promise that either copy
 * stays that way: the package is an ordinary directory the wrap-up's own agent can reach.
 */
export const wrapupFiles = sqliteTable(
  'wrapup_files',
  {
    wrapLaneId: text('wrap_lane_id').notNull(),
    sourceLaneId: text('source_lane_id').notNull(),
    /** Relative to the source session's own folder. */
    path: text('path').notNull(),
    /** Relative to the package directory. */
    storedPath: text('stored_path').notNull(),
    bytes: integer('bytes').notNull(),
    sha256: text('sha256').notNull(),
  },
  (t) => [primaryKey({ columns: [t.wrapLaneId, t.storedPath] })],
);

export type LaneRow = typeof lanes.$inferSelect;
export type RunRow = typeof runs.$inferSelect;
export type EventRow = typeof events.$inferSelect;
export type GroupRow = typeof fanoutGroups.$inferSelect;
export type WorktreeRow = typeof worktrees.$inferSelect;
export type WrapUpRow = typeof wrapups.$inferSelect;
export type WrapUpSourceRow = typeof wrapupSources.$inferSelect;
export type WrapUpRunRow = typeof wrapupRuns.$inferSelect;
export type WrapUpFileRow = typeof wrapupFiles.$inferSelect;
