-- A worktree stops being a race.
--
-- `group_id` was NOT NULL, which made "an isolated checkout" and "a member of a fan-out race" the
-- same fact. Giving one session its own worktree therefore meant inventing a one-member race: a
-- group row, a lane Compare would list, a "Race" pill in the header, and a candidate the keep and
-- supersede rules would reason about. Race membership becomes what it always meant — optional.
--
-- `repo_root` records the repository the worktree belongs to, so it does not have to be re-derived
-- from a path on disk. It is what answers "another session in this project" from inside a lane whose
-- own cwd is a linked checkout, and what `git worktree remove` must be run from.
--
-- SQLite cannot relax NOT NULL in place, so the table is rebuilt. Every existing row is carried over
-- with its race intact, and its `repo_root` is taken from that race (`fanout_groups.repo_root` —
-- every worktree written so far was inserted in the same transaction that created its group, so the
-- join finds it). A row whose group is somehow missing keeps a NULL root: there is nothing in the
-- old schema that says what its repository was, and a made-up path would be worse than an absent
-- one. `worktreeRepoRoot` is where that case is handled.
PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_worktrees` (
	`lane_id` text PRIMARY KEY NOT NULL,
	`group_id` text,
	`repo_root` text,
	`path` text NOT NULL,
	`branch` text NOT NULL,
	`base_commit` text NOT NULL
);
--> statement-breakpoint
INSERT INTO `__new_worktrees`("lane_id", "group_id", "repo_root", "path", "branch", "base_commit")
  SELECT `w`.`lane_id`, `w`.`group_id`, `g`.`repo_root`, `w`.`path`, `w`.`branch`, `w`.`base_commit`
  FROM `worktrees` `w` LEFT JOIN `fanout_groups` `g` ON `g`.`id` = `w`.`group_id`;--> statement-breakpoint
DROP TABLE `worktrees`;--> statement-breakpoint
ALTER TABLE `__new_worktrees` RENAME TO `worktrees`;--> statement-breakpoint
PRAGMA foreign_keys=ON;
