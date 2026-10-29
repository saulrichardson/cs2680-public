-- The column is a closed domain, enforced by the database as well as by the type. The CHECK is
-- written here rather than in the Drizzle snapshot deliberately: SQLite can add a column-level
-- CHECK in an ALTER, which keeps this migration purely additive, whereas declaring it in the schema
-- would make drizzle-kit rebuild the whole table. A later `drizzle-kit generate` therefore does not
-- know about this constraint; do not let it re-add one.
ALTER TABLE `runs` ADD `origin` text DEFAULT 'execution' NOT NULL CHECK (`origin` IN ('execution', 'replay', 'import'));--> statement-breakpoint
-- Backfill, in order of how much the row itself tells us.
--
-- A replay is unambiguous: only `Replayer.start` ever wrote `replay_of`.
UPDATE `runs` SET `origin` = 'replay' WHERE `replay_of` IS NOT NULL;--> statement-breakpoint
-- An import has no column of its own in the old schema, so it is recognised by the marks every
-- version of `Replayer.import` left. Its history was read before this rule was written
-- (`git log -- src/server/replay.ts`), and across all of them the writer:
--
--   * generated the prompt `Imported: <label>` and never took one from the operator;
--   * wrote `permission: 'allowlist'` unconditionally;
--   * never set `group_id` (only a fan-out does) or `resumed_from` (only a follow-up does);
--   * never set `exit_code`, `signal` or `stderr_tail` — those come from a process, and an import
--     has none;
--   * and wrote the run in a terminal state, never `running`.
--
-- What it did *not* do consistently is leave the session id null: the original writer stored the
-- recording's own session (`751930d`), and the runtime database still holds such a row —
-- `pSwZ0X4HVLXn`, session `s-import-1`, the very id whose `--resume` failure is quoted in
-- `repo.isResumeCandidate`. A rule keyed on "no session" would read it as an execution and offer it
-- to `--resume` again, so the session id is deliberately not part of this test.
--
-- `substr(...) = ` rather than `LIKE`: SQLite's LIKE ignores ASCII case, and `imported: ` typed by
-- an operator is not this writer's prompt.
--
-- The two status conditions exclude an execution that never reached the manager's close handler —
-- a run interrupted by a server crash has all three process columns null too. Before the next boot
-- it is still `running`; after it, `failOrphanRuns` has written exactly 'server restarted'.
--
-- RESIDUAL AMBIGUITY, deliberately not chased further: an execution that never spawned at all (a
-- bad directory, a spawn failure) in an ungrouped lane with default permission, whose operator-typed
-- prompt begins exactly `Imported: `, is read as a recording. Such a run has no session, no events
-- and no effect on any directory, so it loses nothing it had. In the other direction, an import
-- written by some future writer that breaks one of these invariants stays an execution; there is no
-- automatic repair for that, and the correction is a manual
-- `UPDATE runs SET origin = 'import' WHERE id = ...`.
UPDATE `runs` SET `origin` = 'import'
  WHERE `replay_of` IS NULL
    AND `group_id` IS NULL
    AND `resumed_from` IS NULL
    AND `permission` = 'allowlist'
    AND substr(`prompt`, 1, 10) = 'Imported: '
    AND `exit_code` IS NULL
    AND `signal` IS NULL
    AND `stderr_tail` IS NULL
    AND `status` <> 'running'
    AND (`error_message` IS NULL OR `error_message` <> 'server restarted');
