# Architecture

Mission Control uses TypeScript, Next.js and React, tRPC with Zod validation, WebSocket streaming, and SQLite through Drizzle. A custom Node server owns the authenticated Claude Code CLI processes. Claude Code supplies the agent loop.

## Execution flow

1. The interface submits a prompt and working directory through tRPC.
2. The server records a run and starts `claude -p` with streaming JSON and forwarded subagent text. Follow-ups use the saved Claude session ID with `--resume`.
3. Raw events are persisted with a run ID and sequence number, then streamed to subscribed clients.
4. The shared reducer derives assistant text, paired tools and results, delegate relationships, status, and usage.
5. The server records the outcome and result-event metrics. History and playback use the same event interpretation as live execution.

Every execution requests Chrome access. The interface distinguishes requested access, available tools, and observed tool outcomes.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `assignment1/server.ts` | Local HTTP server, WebSocket attachment, startup and shutdown |
| `src/server/process/` | CLI arguments, environment, process ownership, cancellation and failures |
| `src/core/` | Event reduction, tool pairing, native delegation and view models |
| `src/server/db/` | Sessions, runs, raw events and workspace records |
| `src/server/ws/` | Ordered delivery, lifecycle updates and reconnect backfill |
| `src/server/workspace/` | Bounded file reads and preview access |
| `src/server/wrapup/` | Captured source reports, file copies and provenance for wrap-up sessions |
| `src/server/git/` | Checkout preparation and Git diffs |
| `src/ui/` | Conversation, outline, delegate inspection and project navigation |

Paths under `src/` are relative to `assignment1/`.

## State and lifecycle

The `lanes` table stores sessions, including project membership and working directory. `runs` stores one prompt's execution or playback, status, metrics, and Claude session ID. `events` retains the original JSON keyed by run and sequence. `worktrees` records managed checkout ownership. The retained comparison implementation also uses `fanout_groups`.

Project membership is independent of execution directory, so isolated checkouts remain associated with their source project. One execution or paced playback may be active per session. Archive hides an idle session without deleting its files.

New sessions opened from a project default to its root folder. New sessions opened inside a session default to that session's working folder, including an isolated checkout.

The server owns work independently of browser navigation. On reconnect it supplies current run inventory and status, backfills missing events, and resumes live delivery. On startup it marks interrupted runs as failed. Cancellation tracks the child process until exit, separately from recording the run's outcome.

Session creation, renaming, archiving, and reopening notify every connected tab to refresh its inventory. Saved workspace selections are reconciled with that inventory before display.

Native delegate completion follows Claude's task lifecycle events; a launch acknowledgement does not mean the child has finished. Parent tool-call identifiers associate child events with their spawning call.

## Files and boundaries

SQLite history and project files have separate lifecycles. File previews read the current workspace, including pre-existing files. Replay restores recorded events, not filesystem snapshots. HTML/SVG previews use a sandboxed iframe and restrictive content policy.

The server binds only to loopback and checks the local request boundary. CLI children use the user's login; inherited Claude session markers and Anthropic credential/endpoint overrides are removed. The working directory is not a filesystem sandbox.

Isolated checkouts start at the selected source's committed HEAD. Preparation failures roll back resources created by that operation. Dependencies, uncommitted changes, and conversation context are not copied.

## Parallel work and wrap-ups

Project selection stays scoped to recorded project membership, including isolated checkouts and wrap-ups. The workspace reuses the focused session component and remembers selected columns in browser storage. Native delegates remain children of their spawning run. Clickable cards select agents at one level; their detail views expose child agents and breadcrumbs back to their parents. The tool-call outline remains beside the dialogue on wide views and becomes a drawer in narrow views. Assigned tasks start collapsed so activity is immediately accessible.

Bring together captures one to three idle sessions into a separate folder under the console data directory. The package includes run identities, prompts, final replies, delegate reports, and operator-selected text files with hashes. `wrapups` and its source/run/file tables preserve the input inventory independently of later source changes. Running sources are refused; failed or stopped sources require acknowledgement. Revision checks reject sources continued while the dialog was open. Captured inputs are ordinary files, not an operating-system sandbox or immutable storage.

Capture enforces file and package bounds, rejects workspace escapes, and rolls back only its own unused directory on failure. The new session runs through the existing CLI manager and supports normal follow-ups. No automatic Git merge occurs.

## Verification and delivery

Use the commands in the [application guide](../README.md#development). Test event handling against recorded fixtures, then verify live streaming, continuation, cancellation, and browser access with the real CLI. Documentation-only changes require link and content checks rather than application test runs.

Keep automated tests focused on execution, event history, delegation, reconnects, saved work, and file boundaries. Exercise process lifecycles where they are the behavior under test; use saved records for rules about completed runs. Presentation details and timing benchmarks belong in manual checks. Assignment 1 CI runs only for changes within `assignment1/` or its workflow.

Keep completed changes on `main`. Avoid extra development worktrees unless requested, and do not restart an active user-testing server as part of a source edit. Preserve concise implementation rationale in [decisions](records/README.md); keep audit transcripts, recording assets, and future plans outside the repository.
