# Assignment 1: Mission Control

Mission Control is a local web interface for working with Claude Code. You choose
a project, submit a task, and follow the conversation alongside the commands,
delegated work, and files it produces. You can inspect the result and continue the
same conversation with a follow-up.

The assignment explores how to make an agent's activity understandable while the
work is happening. A long transcript mixes discussion, execution, and parallel
investigations. Mission Control gives those activities distinct views while keeping
their connection to the original session. The interface supports several sessions
in one project, side-by-side comparison, and a new conversation built from selected
results.

Claude Code performs the agent work. This application manages the local process,
interprets its event stream, persists session state, and presents the work in the
browser. The [architecture guide](docs/approach.md) follows that path from CLI output
to server state and UI components.

## Setup

Requires **Node.js 22+**, **pnpm 10**, and **Claude Code installed, on your PATH, and signed in**. Authenticate the CLI using your own account.

From the repository root:

```bash
cd assignment1
pnpm install --frozen-lockfile
pnpm dev
```

After the server starts, open the URL printed in the terminal: **http://127.0.0.1:3000** by default. This address serves the application on your computer. If you set `PORT`, use that port instead.

To run a production build from `assignment1/`:

```bash
pnpm build
pnpm start
```

For browser tasks, install the [Claude in Chrome extension](https://code.claude.com/docs/en/chrome) and open Chrome. Browser access is requested automatically; the interface reports tool availability and connection errors.

## Use the application

1. Choose a project folder on Home, or use **New folder** to create a scratch directory.
2. Submit a task, such as “Create a one-page countdown timer with start, pause, and reset controls.”
3. Follow the live conversation. Expand tool inputs and results, navigate with the call outline, and inspect generated files in **Files**.
4. Continue with “Add keyboard shortcuts” to retain the conversation's context. Ask Claude to use a subagent when you want delegated work; select the child to inspect its activity and report.

Tool calls show pending, completed, or error states. Runs show their outcome and reported cost, duration, and turn count. Long results and child activity can be folded away.

Sessions are grouped by project. A new session is a separate conversation sharing the selected folder by default. An optional isolated Git checkout starts from committed files; it does not copy uncommitted changes or conversation context. **Stop** interrupts work. **Archive** hides an idle session without deleting its history or files; reopen it from project history.

## Work on several sessions at once

Open a project — from Home, the breadcrumb, or **Side by side** in the session list — to place up to three of its sessions beside each other. Each column is the whole session: conversation, delegates, Files, and its own composer. Choose sessions from the list on the left; the selection is remembered per project across reloads, and narrow screens show one column with a switcher.

**Bring together** starts a new session from what those sessions produced. It captures a package — each source's prompts, final replies, delegate reports, and any text files you select — into a folder of its own under the data directory, and gives the new session your instructions plus that folder to read. Sources must not be running; ones that failed, were stopped, or never ran need an explicit acknowledgement that the work is partial. Original sessions and their files remain separate. The wrap-up's **Inputs** tab lists what was captured, with a link to each source and the SHA-256 of every copied file; its own results are in **Files**.

Recorded runs can be replayed or imported through the command palette (**⌘K / Ctrl+K**). Playback is labeled and starts no agent.

## Configuration

Set environment variables before starting the server. See [config.ts](src/server/config.ts) for validation.

| Variable | Default |
| --- | --- |
| `PORT` | `3000` |
| `HOST` | `127.0.0.1` (loopback only) |
| `MISSION_CONTROL_DATA_DIR` | `assignment1/.data` |
| `MISSION_CONTROL_CLAUDE_BIN` | `claude` |
| `MISSION_CONTROL_SCRATCH_ROOT` | `~/scratch` |
| `MISSION_CONTROL_ALLOWED_TOOLS` | [Standard tool allowlist](src/server/process/args.ts) |

Use absolute paths for directory overrides. Separate running instances should use different ports and data directories.

## Operating notes

- The server is local only and has no authentication. The agent's working directory is not a filesystem sandbox; choose files appropriate for it to read and edit.
- **Files** shows the current filesystem, including pre-existing files. Replay reconstructs a conversation, not earlier file contents.
- Embedded HTML/SVG previews restrict network access. Open an app's own localhost server in a browser for normal web behavior.
- Archiving does not remove Git worktrees. Isolated checkouts do not automatically install dependencies or merge their results.
- Process-group cancellation supports macOS/Linux; Windows cleanup has not been verified.

If a run cannot start, check `claude --version`, CLI authentication, and the selected directory. The run's error includes available diagnostic output. For Chrome failures, check the extension connection. If the port is occupied, choose another `PORT`.

## Development

From `assignment1/`:

```bash
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

The [replay fixtures](fixtures/README.md) include sanitized recordings and synthetic
streams for event-processing tests. They exercise the reducer and replay paths
without starting an agent. Live streaming, continuation, and browser access are
checked separately with a signed-in CLI.

For the purpose behind the interface, read [product intent](docs/product-intent.md).
For implementation details, continue with [architecture](docs/approach.md) and the
[stream format reference](docs/reference/claude-stream-json-types.md). The
[documentation guide](docs/README.md) connects these reading paths.

## Code map

| Area | Source |
| --- | --- |
| HTTP server and startup | [server.ts](server.ts) |
| Agent execution and cancellation | [src/server/process/](src/server/process/) |
| Event interpretation and subagents | [src/core/](src/core/) |
| Interface | [src/app/](src/app/) and [src/ui/](src/ui/) |
| API, storage, and streaming | [tRPC](src/server/trpc/router.ts), [database](src/server/db/), [WebSocket hub](src/server/ws/hub.ts) |
| Files and isolated checkouts | [workspace files](src/server/workspace/files.ts), [Git worktrees](src/server/git/worktrees.ts) |

See the [architecture guide](docs/approach.md) for the data flow and the
[documentation guide](docs/README.md) for further reading.
