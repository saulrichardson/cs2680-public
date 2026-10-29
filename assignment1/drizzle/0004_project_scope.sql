-- Project scope, archiving, and the end of the per-session browser switch.
--
-- Three changes to `lanes`, all preserving: no row is deleted and no recorded execution is
-- rewritten. `runs.browser` is untouched, so an old trajectory still says what its run was
-- actually launched with and a replay of it stays truthful.
--
-- 1. `project_root` — the folder the operator chose, recorded separately from `cwd`.
--
--    They are the same thing for an ordinary session and very different for an isolated one, whose
--    `cwd` is a git worktree under the console's data directory. Grouping by `cwd` therefore split
--    a project's own sessions apart the moment one of them took a checkout.
--
--    Backfilled from the strongest evidence each row has: the repository recorded for the managed
--    worktree this session works in, and otherwise the session's own `cwd`. A path prefix or a name
--    is not evidence and is not used. `''` cannot survive the backfill — every lane has a `cwd` —
--    but the column is declared with that default so the ALTER stays additive; the server
--    canonicalises and writes a real value on every new session.
--
-- 2. `closed_at` becomes `archived_at`. Same column, honest name: it never meant the session's
--    files or history went anywhere, and it is now reversible through `lanes.reopen`.
--
-- 3. `lanes.browser` is dropped. The browser is no longer a per-session setting an operator can
--    turn on or off — every new execution is launched with it (`buildClaudeArgs`) — so keeping a
--    mutable column for it would leave a value nothing reads and a route that could contradict the
--    policy. What each *run* was launched with stays on `runs.browser`, which is where the
--    trajectory, the browser evidence and replay have always read it from.
ALTER TABLE `lanes` ADD `project_root` text DEFAULT '' NOT NULL;--> statement-breakpoint
UPDATE `lanes`
SET `project_root` = COALESCE(
  (SELECT `w`.`repo_root` FROM `worktrees` `w` WHERE `w`.`lane_id` = `lanes`.`id` AND `w`.`repo_root` IS NOT NULL),
  `cwd`
);--> statement-breakpoint
ALTER TABLE `lanes` RENAME COLUMN `closed_at` TO `archived_at`;--> statement-breakpoint
ALTER TABLE `lanes` DROP COLUMN `browser`;
