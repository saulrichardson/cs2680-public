CREATE TABLE `events` (
	`run_id` text NOT NULL,
	`seq` integer NOT NULL,
	`received_at` integer NOT NULL,
	`type` text NOT NULL,
	`parent_tool_use_id` text,
	`json` text NOT NULL,
	PRIMARY KEY(`run_id`, `seq`)
);
--> statement-breakpoint
CREATE TABLE `fanout_groups` (
	`id` text PRIMARY KEY NOT NULL,
	`prompt` text NOT NULL,
	`repo_root` text NOT NULL,
	`base_commit` text NOT NULL,
	`created_at` integer NOT NULL,
	`kept_run_id` text
);
--> statement-breakpoint
CREATE TABLE `lanes` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`cwd` text NOT NULL,
	`permission` text NOT NULL,
	`group_id` text,
	`group_index` integer,
	`created_at` integer NOT NULL,
	`closed_at` integer
);
--> statement-breakpoint
CREATE TABLE `runs` (
	`id` text PRIMARY KEY NOT NULL,
	`lane_id` text NOT NULL,
	`group_id` text,
	`prompt` text NOT NULL,
	`effective_cwd` text NOT NULL,
	`permission` text NOT NULL,
	`session_id` text,
	`resumed_from` text,
	`replay_of` text,
	`status` text NOT NULL,
	`started_at` integer NOT NULL,
	`ended_at` integer,
	`exit_code` integer,
	`signal` text,
	`stderr_tail` text,
	`error_message` text,
	`cost_usd` real,
	`duration_ms` integer,
	`duration_api_ms` integer,
	`num_turns` integer,
	`model` text,
	`max_turns` integer
);
--> statement-breakpoint
CREATE INDEX `runs_lane_started_idx` ON `runs` (`lane_id`,`started_at`);--> statement-breakpoint
CREATE INDEX `runs_group_idx` ON `runs` (`group_id`);--> statement-breakpoint
CREATE TABLE `worktrees` (
	`lane_id` text PRIMARY KEY NOT NULL,
	`group_id` text NOT NULL,
	`path` text NOT NULL,
	`branch` text NOT NULL,
	`base_commit` text NOT NULL
);
