-- Whether a session drives the Claude in Chrome extension, and what each run was actually launched
-- with. Two columns rather than one because they answer different questions: the lane's is a
-- setting for the next run, the run's is a fact about a trajectory that must not change when the
-- setting does.
--
-- Additive, with a `CHECK` written here rather than in the schema for the same reason as
-- `0001_add_run_origin`: SQLite can add a column-level CHECK in an `ALTER`, so the migration stays
-- additive, while declaring it in the Drizzle schema would make drizzle-kit rebuild the table. A
-- later `drizzle-kit generate` does not know about these constraints; do not let it re-add them.
--
-- Every existing row defaults to `off`. Read that as "Mission Control did not ask for the browser",
-- which is all it can mean: nothing in the old console passed a Chrome flag. It is *not* a claim
-- that no old run touched a browser — a wrapper script around the CLI, or Chrome enabled by default
-- in the operator's own Claude Code settings, could both have given one browser tools. Where that
-- happened the evidence is still where it always was, in the run's own events, and
-- `deriveBrowserView` reports a recorded browser call whatever this column says.
ALTER TABLE `lanes` ADD `browser` text DEFAULT 'off' NOT NULL CHECK (`browser` IN ('off', 'chrome'));--> statement-breakpoint
ALTER TABLE `runs` ADD `browser` text DEFAULT 'off' NOT NULL CHECK (`browser` IN ('off', 'chrome'));
