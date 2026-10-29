-- Wrap-ups: a session handed a captured package of other sessions' work.
--
-- Purely additive: four new tables, nothing existing is touched, and every existing lane is simply
-- a lane with no wrap-up row. Four tables rather than one with a `kind` column, because they answer
-- four questions — what the package is (wrapups), which sessions it came from as they stood
-- (wrapup_sources), which of their executions it covers (wrapup_runs), and which files were copied
-- with the hash of the bytes copied (wrapup_files). `wrapup_sources_source_idx` answers the
-- question in the other direction: which wrap-ups used this session.
CREATE TABLE `wrapup_files` (
	`wrap_lane_id` text NOT NULL,
	`source_lane_id` text NOT NULL,
	`path` text NOT NULL,
	`stored_path` text NOT NULL,
	`bytes` integer NOT NULL,
	`sha256` text NOT NULL,
	PRIMARY KEY(`wrap_lane_id`, `stored_path`)
);
--> statement-breakpoint
CREATE TABLE `wrapup_runs` (
	`wrap_lane_id` text NOT NULL,
	`source_lane_id` text NOT NULL,
	`run_id` text NOT NULL,
	`ordinal` integer NOT NULL,
	`status` text NOT NULL,
	`prompt` text NOT NULL,
	PRIMARY KEY(`wrap_lane_id`, `run_id`)
);
--> statement-breakpoint
CREATE TABLE `wrapup_sources` (
	`wrap_lane_id` text NOT NULL,
	`source_lane_id` text NOT NULL,
	`ordinal` integer NOT NULL,
	`name` text NOT NULL,
	`outcome` text NOT NULL,
	`revision` text NOT NULL,
	PRIMARY KEY(`wrap_lane_id`, `source_lane_id`)
);
--> statement-breakpoint
CREATE INDEX `wrapup_sources_source_idx` ON `wrapup_sources` (`source_lane_id`);--> statement-breakpoint
CREATE TABLE `wrapups` (
	`lane_id` text PRIMARY KEY NOT NULL,
	`project_root` text NOT NULL,
	`version` integer NOT NULL,
	`captured_at` integer NOT NULL,
	`dir` text NOT NULL,
	`instructions` text NOT NULL,
	`partial` integer NOT NULL
);
