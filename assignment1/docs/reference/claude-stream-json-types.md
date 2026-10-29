# Claude Code stream-json event types

Reference for the events `claude -p --output-format stream-json --verbose` prints, one JSON object per line.

- Source: type declarations in `@anthropic-ai/claude-agent-sdk@0.3.273` (`sdk.d.ts`), extracted 2026-09-16. The SDK is **not** used by this project; its published types are the authoritative description of the CLI's wire format, which is what Mission Control consumes. CLI in use when the fixtures were recorded: 2.1.270.
- Observed reality is in `assignment1/fixtures/*.jsonl`, plus the runs of the 2026-09-19 end-to-end audit against CLI 2.1.270, whose shapes are written up in their own section below. Where the declarations and what came down the wire disagree, the wire wins and this file should be updated.
- Doc comments are trimmed to their first 160 characters. `UUID` is a string. `BetaMessage`, `MessageParam`, `BetaUsage`, and `BetaRawMessageStreamEvent` are the Anthropic Messages API types.

## Observations from the recorded fixtures

- Assistant messages arrive **one content block per event**; events of the same model turn share `message.id`. The `usage` on each block-event is the partial usage at stream start (tiny `output_tokens`); authoritative totals arrive in `result.usage` and `result.modelUsage`.
- Content block types seen: `text`, `thinking` (with empty `thinking` text and a `signature`), `tool_use` (with `caller`), and on user events `tool_result` and `text`.
- `assistant` and `user` events carry an ISO `timestamp`; `system` events, `rate_limit_event`, and `tool_progress` do not. Use receive time for those.
- The delegate tool is named **`Agent`** in CLI 2.1.270 (older material says `Task`). Subagent events carry `parent_tool_use_id` = the `Agent` call's `tool_use` id. The subagent's first event is a `user` text message with its prompt. Its own tool calls and results follow with the same parent. Its text blocks appear only with `--forward-subagent-text`. The `Agent` call's `tool_result` arrives on the main thread (`parent_tool_use_id: null`) with `tool_use_result` containing `agentId`, `agentType`, `content`, `prompt`, `resolvedModel`.
- Subagent lifecycle also arrives as `system` events: `task_started`, `task_progress`, `task_updated`, `task_notification`, plus a `tool_progress` event. The observed payloads and what the reducer takes from each are in *Observed in the 2.1.270 end-to-end runs* below.
- `user` events have `tool_use_result` with per-tool structured output. Bash: `stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected`, and `bashEditDiff` (files → hunks → lines) when the command edited files. Skill: `success`, `commandName`.
- `assistant` tool_use events also carry `wire_tool_inputs` (the command as actually sent, e.g. with a `cd` prefix) and `wire_ingest_context` (cwd).
- Other events seen: `system/hook_started`, `system/hook_response` (the SessionStart hook output, several KB), `system/thinking_tokens` (frequent while the model thinks), `system/task_summary` (`detail`: a one-line description of current activity), `system/post_turn_summary` (`status_category`, `status_detail`), `system/api_retry`, and top-level `rate_limit_event`.
- The `result` event is **not always the last line**; a `system/task_summary` followed it in every recording. Under API retries the reported `duration_ms` (135 s) diverged from wall-clock time (68 min); measure wall time independently.
- Parallel tool calls appear as consecutive `assistant` tool_use events sharing one `message.id`, followed by their results in order.
- A `--max-turns` overrun yields `result` with `subtype: "error_max_turns"`, `is_error: true`, `errors: ["Reached maximum number of turns (2)"]`, `terminal_reason: "max_turns"`, and still carries cost and usage.

## Observed in the 2.1.270 end-to-end runs (2026-09-19)

Shapes seen on the wire during the end-to-end audit, beyond what the committed fixtures contain. **used** marks the fields Mission Control's reducer reads (`src/core/reducer.ts`); everything else is carried in the stored event and visible in the inspector, but nothing in the view model depends on it. Unhandled event types and `system` subtypes fall through to `ignoredCount` — they are counted, never rendered as a notice.

### A delegation that launches asynchronously

The `Agent` call's `tool_result` comes back in milliseconds and is only a launch acknowledgement. Its text is the harness note "Async agent launched successfully. (This tool result is internal metadata — never quote…)", and `tool_use_result` is:

```json
{ "isAsync": true, "status": "async_launched", "agentId": "…", "description": "Survey this repository",
  "resolvedModel": "claude-…", "prompt": "…the full subagent prompt…" }
```

- **used**: `isAsync` / `status` — either one marks the launch, which keeps the call `pending`, flags the result text internal, and sets `task.async`. `status` is also read by the synchronous path's result merge.
- not used: `agentId`, `description`, `resolvedModel`, `prompt` (the description the card shows comes from `task_started`).

A synchronous delegate result — what the recorded fixtures contain — instead carries `agentId`, `agentType`, `content`, `prompt`, `resolvedModel`, `totalTokens`, `totalToolUseCount`, `totalDurationMs`, and resolves the call directly.

### `system/task_started`

```json
{ "type": "system", "subtype": "task_started", "task_id": "…", "tool_use_id": "toolu_…",
  "description": "Survey this repository", "subagent_type": "Explore", "task_type": "local_agent",
  "is_backgrounded": true, "spawn_depth": 1, "prompt": "…", "uuid": "…", "session_id": "…" }
```

- **used**: `tool_use_id` (or `task_id`) to find the call, `task_id`, `description`, `subagent_type`, `spawn_depth`; the subtype itself forces `task.status = running`.
- not used: `is_backgrounded`, `task_type` (`local_agent` for a subagent; other values exist for workflows and MCP tasks), `prompt`.

### `system/task_progress` (many per task)

```json
{ "type": "system", "subtype": "task_progress", "task_id": "…", "tool_use_id": "toolu_…",
  "description": "List the top-level files", "subagent_type": "Explore",
  "usage": { "total_tokens": 21987, "tool_uses": 18, "duration_ms": 35120 },
  "last_tool_name": "Read" }
```

- **used**: `usage.total_tokens`, `usage.tool_uses`, `usage.duration_ms`, `last_tool_name`, and `description` — which on a progress event is the *current activity*, not the original task, and is kept in `task.activity` so it cannot overwrite the description `task_started` gave. `summary`, when the CLI is configured to produce progress summaries, feeds the same slot as the report.

### `system/task_updated`

```json
{ "type": "system", "subtype": "task_updated", "task_id": "…",
  "patch": { "status": "completed", "end_time": 1758240000000 } }
```

- **used**: `task_id`, `patch.status`, `patch.description`. A terminal `patch.status` closes the call the same way a notification does.
- not used: `patch.end_time` (the reducer stamps completion from receive time, so one clock governs a run), `total_paused_ms`, `is_backgrounded`.

### `system/task_notification` — the terminal event

```json
{ "type": "system", "subtype": "task_notification", "task_id": "…", "tool_use_id": "toolu_…",
  "status": "completed", "summary": "## Repository survey\n\n…the full markdown report…",
  "output_file": "/…/task-….md", "usage": { "total_tokens": …, "tool_uses": …, "duration_ms": … } }
```

- **used**: `tool_use_id` / `task_id`, `status` (`completed` | `failed` | `stopped` — the card's final status), `summary` (the report the card renders), and the `usage` totals.
- not used: `output_file`, `reason`, `resource_links`.

### The interrupt sequence on SIGINT

Two lines, in this order, then exit 0:

```json
{ "type": "user", "parent_tool_use_id": null,
  "message": { "role": "user", "content": [{ "type": "text", "text": "[Request interrupted by user]" }] } }
{ "type": "result", "subtype": "error_during_execution", "is_error": true,
  "errors": ["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use"],
  "total_cost_usd": 0.24, "duration_ms": 24000, "num_turns": 15, "usage": { … } }
```

- **used**: the exact text `[Request interrupted by user]` becomes the muted "Stopped by you" notice instead of a user-message fold; the result's numbers and usage are kept as for any result; its `errors` are filtered of `[ede_diagnostic]` strings, which are internal diagnostics and never an operator-facing error. A run the operator stopped ends with no error at all.
- The interrupted result is what makes cancel worth doing at all: the numbers survive the stop.

### `rate_limit_event` (top level, not a `system` subtype)

```json
{ "type": "rate_limit_event", "rate_limit_info": { "status": "allowed", "rateLimitType": "five_hour",
  "utilization": 0.42, "resetsAt": 1758240000 } }
```

- **used**: `rate_limit_info.status`, `.rateLimitType`, `.utilization`, `.resetsAt`.
- not used: the overage fields (`overageStatus`, `isUsingOverage`, `overageResetsAt`, …).

### `system/commands_changed`

```json
{ "type": "system", "subtype": "commands_changed", "commands": [ … ] }
```

Emitted a couple of times per run as the CLI's slash-command set settles. Nothing in the view model uses it; it lands in `ignoredCount`. Listed here because it was new in these runs, alongside the other subtypes the audit saw: `task_summary`, `post_turn_summary`, `thinking_tokens`, `hook_started`, `hook_response`.

## Type declarations

```ts

type SDKMessage = SDKAssistantMessage | SDKUserMessage | SDKUserMessageReplay | SDKResultMessage | SDKSystemMessage | SDKPartialAssistantMessage | SDKCompactBoundaryMessage | SDKStatusMessage | SDKAPIRetryMessage | SDKControlRequestProgressMessage | SDKModelRefusalFallbackMessage | SDKModelRefusalNoFallbackMessage | SDKLocalCommandOutputMessage | SDKHookStartedMessage | SDKHookProgressMessage | SDKHookResponseMessage | SDKPluginInstallMessage | SDKToolProgressMessage | SDKAuthStatusMessage | SDKTaskNotificationMessage | SDKTaskStartedMessage | SDKTaskUpdatedMessage | SDKTaskProgressMessage | SDKBackgroundTasksChangedMessage | SDKThinkingTokensMessage | SDKSessionStateChangedMessage | SDKWorkerShuttingDownMessage | SDKCommandsChangedMessage | SDKNotificationMessage | SDKFilesPersistedEvent | SDKToolUseSummaryMessage | SDKMemoryRecallMessage | SDKRateLimitEvent | SDKElicitationCompleteMessage | SDKPermissionDeniedMessage | SDKPromptSuggestionMessage | SDKMirrorErrorMessage | SDKInformationalMessage | SDKConversationResetMessage;

type SDKAssistantMessage = {
    type: 'assistant';
    /** * Shaped like an Anthropic Messages API Message object (role "assistant"): id, model, content blocks (text, thinking, tool_use, ...), stop_reason and usage. Whe */
    message: BetaMessage;
    parent_tool_use_id: string | null;
    error?: SDKAssistantMessageError;
    uuid: UUID;
    session_id: string;
    request_id?: string;
    /** * Client uuid of the user message this turn is answering (submitMessage options.uuid), stamped on an assistant message each time that send changes — the turn's  */
    user_message_uuid?: string;
    /** * Client uuids of every user message whose prompt this turn has consumed so far, in consumption order — all members of a prompt batch the host merged into this  */
    user_message_uuids?: string[];
    /** * Why this frame's turn is the automatic re-run of a turn a worker restart interrupted (CLAUDE_CODE_RESUME_INTERRUPTED_TURN): the host's CLAUDE_CODE_RESUME_REAS */
    resume_reason?: string;
    /** * This turn continued the preceding truncated assistant turn inside its trailing signed thinking block (max-output-tokens recovery). Its thinking signatures are */
    resumed_from_incomplete_thinking?: true;
    /** * Wire uuids of previously-delivered messages that this message replaces (refusal-fallback supersede). The list can include tombstoned tool_result frames from t */
    supersedes?: UUID[];
    /** * True when this assistant message was truncated by an interrupt/abort before the stream completed: stop_reason was never received and the content may end mid-w */
    aborted?: true;
    /** * Subagent type that produced this message. */
    subagent_type?: string;
    /** * Description of the subagent task that produced this message. */
    task_description?: string;
    /** * ISO timestamp of when this content block finished on the originating process. One API assistant turn may produce several assistant messages sharing a message. */
    timestamp?: string;
    /** * Structured twin of the /context report, carried on the synthetic assistant message that delivers the markdown table. Present only on /context results from CLI */
    context_usage?: SDKContextUsage;
    /** * Structured twin of the /usage report, carried on the synthetic assistant message that delivers its text: the session totals, the plan's usage rows and extra-u */
    usage_report?: SDKUsageReport;
};

type SDKAssistantMessageError = 'authentication_failed' | 'oauth_org_not_allowed' | 'account_on_hold' | 'verification_required' | 'billing_error' | 'rate_limit' | 'overloaded' | 'invalid_request' | 'model_not_found' | 'server_error' | 'unknown' | 'max_output_tokens' | 'cloud_credential_error';

type SDKUserMessage = {
    type: 'user';
    /** * An Anthropic Messages API user message: a MessageParam with role "user" whose content is a string or an array of content blocks (text, image, document, tool_r */
    message: MessageParam;
    parent_tool_use_id: string | null;
    isSynthetic?: boolean;
    /** * Structured tool output — the tool's full Output object, not the string content sent to the model. The shape is per-tool, keyed by the matching tool_use block' */
    tool_use_result?: unknown;
    priority?: 'now' | 'next' | 'later';
    origin?: SDKMessageOrigin;
    /** * When false, the message is appended to the transcript without triggering an assistant turn. It will be merged into the next user message that does query. */
    shouldQuery?: boolean;
    /** * ISO timestamp when the message was created on the originating process. Older emitters omit it; consumers should fall back to receive time. */
    timestamp?: string;
    uuid?: UUID;
    session_id?: string;
    /** * Subagent type that produced this message. */
    subagent_type?: string;
    /** * Description of the subagent task that produced this message. */
    task_description?: string;
};

type SDKUserMessageReplay = {
    type: 'user';
    /** * An Anthropic Messages API user message: a MessageParam with role "user" whose content is a string or an array of content blocks (text, image, document, tool_r */
    message: MessageParam;
    parent_tool_use_id: string | null;
    isSynthetic?: boolean;
    /** * Structured tool output — the tool's full Output object, not the string content sent to the model. The shape is per-tool, keyed by the matching tool_use block' */
    tool_use_result?: unknown;
    priority?: 'now' | 'next' | 'later';
    origin?: SDKMessageOrigin;
    /** * When false, the message is appended to the transcript without triggering an assistant turn. It will be merged into the next user message that does query. */
    shouldQuery?: boolean;
    /** * ISO timestamp when the message was created on the originating process. Older emitters omit it; consumers should fall back to receive time. */
    timestamp?: string;
    uuid: UUID;
    session_id: string;
    isReplay: true;
    file_attachments?: unknown[];
};

type SDKResultMessage = SDKResultSuccess | SDKResultError;

type SDKResultSuccess = {
    type: 'result';
    subtype: 'success';
    duration_ms: number;
    duration_api_ms: number;
    ttft_ms?: number;
    ttft_stream_ms?: number;
    time_to_request_ms?: number;
    user_message_uuid?: string;
    user_message_uuids?: string[];
    resume_reason?: string;
    local_command?: string;
    request_sent_wall_ms?: number;
    first_content_frame_ms?: number;
    first_stream_post_ms?: number;
    first_stream_post_ack_ms?: number;
    first_stream_post_wall_ms?: number;
    time_to_request_from_spawn_ms?: number;
    warm_spare_claimed?: boolean;
    time_origin_ms?: number;
    is_error: boolean;
    api_error_status?: number | null;
    num_turns: number;
    result: string;
    stop_reason: string | null;
    /** * Cumulative estimated cost in USD for this query() call, covering the same query-pipeline calls as modelUsage and sharing its lifecycle: cumulative across turn */
    total_cost_usd: number;
    /** * MAIN AGENT LOOP ONLY — excludes Task subagent, sidechain, and auxiliary model calls, and is per-turn in streaming-input sessions. Prefer modelUsage for token/ */
    usage: NonNullableUsage;
    /** * Per-model totals for every model call made through the query pipeline during this query() call — main loop, Task subagents, sidechains, and internal calls suc */
    modelUsage: Record<string, ModelUsage>;
    permission_denials: SDKPermissionDenial[];
    /** * User-initiated sends still waiting in the command queue when this result was produced. Greater than 0 means at least one more user turn (and result) follows w */
    queued_turn_count?: number;
    structured_output?: unknown;
    deferred_tool_use?: SDKDeferredToolUse;
    terminal_reason?: TerminalReason;
    /** * Delivery sequence of this result within the run: how many results the run numbered before this one, starting at 0, in the order the process writes them. A res */
    result_index?: number;
    fast_mode_state?: FastModeState;
    fast_mode_disabled_reason?: FastModeDisabledReason;
    origin?: SDKMessageOrigin;
    uuid: UUID;
    session_id: string;
};

type SDKResultError = {
    type: 'result';
    subtype: 'error_during_execution' | 'error_max_turns' | 'error_max_budget_usd' | 'error_max_structured_output_retries';
    duration_ms: number;
    duration_api_ms: number;
    is_error: boolean;
    num_turns: number;
    stop_reason: string | null;
    /** * Cumulative estimated cost in USD for this query() call, covering the same query-pipeline calls as modelUsage and sharing its lifecycle: cumulative across turn */
    total_cost_usd: number;
    /** * MAIN AGENT LOOP ONLY — excludes Task subagent, sidechain, and auxiliary model calls, and is per-turn in streaming-input sessions. Prefer modelUsage for token/ */
    usage: NonNullableUsage;
    /** * Per-model totals for every model call made through the query pipeline during this query() call — main loop, Task subagents, sidechains, and internal calls suc */
    modelUsage: Record<string, ModelUsage>;
    permission_denials: SDKPermissionDenial[];
    /** * User-initiated sends still waiting in the command queue when this result was produced. Greater than 0 means at least one more user turn (and result) follows w */
    queued_turn_count?: number;
    errors: string[];
    /** * Client uuid of the user message that triggered this turn (submitMessage options.uuid), echoed back so a consumer can link this error result to the send it ans */
    user_message_uuid?: string;
    /** * Client uuids of every user message whose prompt this turn consumed, in consumption order — all members of a prompt batch the host merged into this one turn (s */
    user_message_uuids?: string[];
    /** * Why this turn was the automatic re-run of a turn a worker restart interrupted (CLAUDE_CODE_RESUME_INTERRUPTED_TURN): the host's CLAUDE_CODE_RESUME_REASON when */
    resume_reason?: string;
    terminal_reason?: TerminalReason;
    /** * Delivery sequence of this result within the run: how many results the run numbered before this one, starting at 0, in the order the process writes them. A res */
    result_index?: number;
    fast_mode_state?: FastModeState;
    fast_mode_disabled_reason?: FastModeDisabledReason;
    origin?: SDKMessageOrigin;
    uuid: UUID;
    session_id: string;
};

type ModelUsage = {
    inputTokens: number;
    outputTokens: number;
    /** * Thinking tokens, already counted inside outputTokens. Counts only turns run on CLI versions that record this field: absent when none did, and partial for a re */
    thinkingTokens?: number;
    cacheReadInputTokens: number;
    cacheCreationInputTokens: number;
    webSearchRequests: number;
    costUSD: number;
    contextWindow: number;
    maxOutputTokens: number;
    /** * Canonical model id used for the pricing lookup (e.g. 'claude-opus-4-7'). May differ from the raw model string this entry is keyed by (provider-specific ids, a */
    canonicalModel?: string;
    /** * API provider that served this model (e.g. 'firstParty', 'bedrock', 'vertex', 'foundry', 'anthropicAws', 'mantle', 'gateway'). */
    provider?: string;
    /** * Which price table the most recent request for this model was priced at: Claude Code's built-in list prices ('list'), the organization's managed-settings model */
    costBasis?: 'list' | 'managed' | 'unknown';
};

type NonNullableUsage = {
    [K in keyof BetaUsage]: NonNullable<BetaUsage[K]>;
};

type SDKSystemMessage = {
    type: 'system';
    subtype: 'init';
    agents?: string[];
    /** * Where the credential used for API requests came from: 'ANTHROPIC_API_KEY' (environment variable), 'apiKeyHelper' (the configured helper command), '/login mana */
    apiKeySource: ApiKeySource;
    betas?: string[];
    claude_code_version: string;
    cwd: string;
    tools: string[];
    mcp_servers: {
        name: string;
        status: string;
    }[];
    model: string;
    /** * Permission mode for controlling how tool executions are handled. 'default' - Standard behavior, prompts for dangerous operations. 'acceptEdits' - Auto-accept  */
    permissionMode: PermissionMode;
    slash_commands: string[];
    /** * Subset of slash_commands whose UX is bound to the local terminal (e.g. exit, statusline). Phone/remote UIs should hide these from command menus; desktop surfa */
    terminal_slash_commands?: string[];
    output_style: string;
    skills: string[];
    plugins: {
        name: string;
        path: string;
        /** * The plugin's version as declared in its plugin.json manifest, emitted verbatim (plugin-author-controlled — validate before trusting). Omitted when the manifes */
        version?: string;
    }[];
    fast_mode_state?: FastModeState;
    fast_mode_disabled_reason?: FastModeDisabledReason;
    /** * The effort level the session will send on its next request — after env overrides, session state, org caps and model-support downgrades; the same value get_set */
    effort?: ('low' | 'medium' | 'high' | 'xhigh' | 'max') | null;
    /** * Protocol capabilities this CLI supports, so SDK consumers can feature-detect instead of version-sniffing. Open set — ignore unknown values; check each capabil */
    capabilities?: string[];
    uuid: UUID;
    session_id: string;
};

type PermissionMode = 'default' | 'acceptEdits' | 'bypassPermissions' | 'plan' | 'dontAsk' | 'auto';

type ApiKeySource = 'ANTHROPIC_API_KEY' | 'apiKeyHelper' | '/login managed key' | 'none' | 'user' | 'project' | 'org' | 'temporary' | 'oauth';

type FastModeState = 'off' | 'cooldown' | 'on';

type SDKPartialAssistantMessage = {
    type: 'stream_event';
    /** * One Anthropic Messages API streaming event (message_start, content_block_start, content_block_delta, content_block_stop, message_delta, message_stop) as defin */
    event: BetaRawMessageStreamEvent;
    parent_tool_use_id: string | null;
    uuid: UUID;
    session_id: string;
    ttft_ms?: number;
    /** * Client uuid of the user message this turn is answering (submitMessage options.uuid), stamped on a non-ping stream event each time that send changes: the turn' */
    user_message_uuid?: string;
    /** * Client uuids of every user message whose prompt this turn has consumed so far, in consumption order — all members of a prompt batch the host merged into this  */
    user_message_uuids?: string[];
    /** * Why this frame's turn is the automatic re-run of a turn a worker restart interrupted (CLAUDE_CODE_RESUME_INTERRUPTED_TURN): the host's CLAUDE_CODE_RESUME_REAS */
    resume_reason?: string;
};

type SDKCompactBoundaryMessage = {
    type: 'system';
    subtype: 'compact_boundary';
    compact_metadata: {
        trigger: 'manual' | 'auto';
        pre_tokens: number;
        post_tokens?: number;
        duration_ms?: number;
        /** * Relink info for messagesToKeep. Loaders splice the preserved segment at anchor_uuid (summary for suffix-preserving, boundary for prefix-preserving partial com */
        preserved_segment?: {
            head_uuid: UUID;
            anchor_uuid: UUID;
            tail_uuid: UUID;
        };
        /** * Ordered messagesToKeep UUIDs. Supersedes preserved_segment — readers look up each UUID directly and relink uuids[i] to uuids[i-1] (uuids[0] to anchor_uuid) in */
        preserved_messages?: {
            anchor_uuid: UUID;
            uuids: UUID[];
        };
    };
    uuid: UUID;
    session_id: string;
};

type SDKStatusMessage = {
    type: 'system';
    subtype: 'status';
    status: SDKStatus;
    permissionMode?: PermissionMode;
    compact_result?: 'success' | 'failed';
    compact_error?: string;
    uuid: UUID;
    session_id: string;
};

type SDKStatus = 'compacting' | 'requesting' | null;

type SDKAPIRetryMessage = {
    type: 'system';
    subtype: 'api_retry';
    attempt: number;
    max_retries: number;
    retry_delay_ms: number;
    error_status: number | null;
    error: SDKAssistantMessageError;
    /** * Present only when the API sent no response headers within the first-byte window (CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS): waited_ms is how long the failed attemp */
    no_response?: {
        waited_ms: number;
        retry_wait_ms: number;
    };
    uuid: UUID;
    session_id: string;
};

type SDKHookStartedMessage = {
    type: 'system';
    subtype: 'hook_started';
    hook_id: string;
    hook_name: string;
    hook_event: string;
    uuid: UUID;
    session_id: string;
};

type SDKHookProgressMessage = {
    type: 'system';
    subtype: 'hook_progress';
    hook_id: string;
    hook_name: string;
    hook_event: string;
    stdout: string;
    stderr: string;
    output: string;
    uuid: UUID;
    session_id: string;
};

type SDKHookResponseMessage = {
    type: 'system';
    subtype: 'hook_response';
    hook_id: string;
    hook_name: string;
    hook_event: string;
    output: string;
    stdout: string;
    stderr: string;
    exit_code?: number;
    outcome: 'success' | 'error' | 'cancelled';
    uuid: UUID;
    session_id: string;
};

type SDKThinkingTokensMessage = {
    type: 'system';
    subtype: 'thinking_tokens';
    estimated_tokens: number;
    estimated_tokens_delta: number;
    /** * Client uuid of the user message that triggered this turn (submitMessage options.uuid), stamped on every thinking_tokens frame of a headless (stream-json / Age */
    user_message_uuid?: string;
    uuid: UUID;
    session_id: string;
};

type SDKTaskStartedMessage = {
    type: 'system';
    subtype: 'task_started';
    task_id: string;
    tool_use_id?: string;
    description: string;
    /** * Subagent type for Task tool subagents. */
    subagent_type?: string;
    /** * Whether the task was registered in the background (true) or in the foreground with the spawning tool call blocking on it (false). A resumed subagent is always */
    is_backgrounded?: boolean;
    /** * Nesting depth of a spawned subagent (local_agent) task: 1 for a top-level spawn, N+1 when spawned from inside a depth-N agent. Not set on other tasks. */
    spawn_depth?: number;
    task_type?: string;
    /** * meta.name from the workflow script (e.g. 'spec'). Only set when task_type is 'local_workflow'. */
    workflow_name?: string;
    prompt?: string;
    /** * Ambient/housekeeping task. Consumers should hide this from the inline transcript; it may still appear in a tasks panel. */
    skip_transcript?: boolean;
    /** * True for tasks that are not activity (every skip_transcript task, plus every live-update watcher, requested or auto-started); hosts should exclude them from a */
    ambient?: boolean;
    uuid: UUID;
    session_id: string;
};

type SDKTaskProgressMessage = {
    type: 'system';
    subtype: 'task_progress';
    task_id: string;
    tool_use_id?: string;
    description: string;
    /** * Subagent type for Task tool subagents. */
    subagent_type?: string;
    usage: {
        total_tokens: number;
        tool_uses: number;
        duration_ms: number;
    };
    last_tool_name?: string;
    /** * A one-line status for the task's row. For a local_agent task it is the model-generated progress summary (only when the agentProgressSummaries option is on); f */
    summary?: string;
    uuid: UUID;
    session_id: string;
};

type SDKTaskUpdatedMessage = {
    type: 'system';
    subtype: 'task_updated';
    task_id: string;
    /** * Wire-safe subset of TaskState fields that changed. Excludes abortController, messages, result. Clients merge into their local task map. */
    patch: {
        status?: 'pending' | 'running' | 'completed' | 'failed' | 'killed' | 'paused';
        description?: string;
        end_time?: number;
        total_paused_ms?: number;
        error?: string;
        is_backgrounded?: boolean;
    };
    uuid: UUID;
    session_id: string;
};

type SDKTaskNotificationMessage = {
    type: 'system';
    subtype: 'task_notification';
    task_id: string;
    tool_use_id?: string;
    status: 'completed' | 'failed' | 'stopped';
    /** * Machine-readable cause, set only when the task did not end through an ordinary completion, failure, or stop. 'worker_restart': the worker process restarted an */
    reason?: 'worker_restart';
    output_file: string;
    summary: string;
    usage?: {
        total_tokens: number;
        tool_uses: number;
        duration_ms: number;
    };
    /** * CLI-owned: for a backgrounded MCP task (task_type mcp_task) that completed, the `resource_link` content blocks of its final result — the files it returned by  */
    resource_links?: SDKMcpResourceLink[];
    skip_transcript?: boolean;
    /** * True for tasks that are not activity (every skip_transcript task, plus every live-update watcher, requested or auto-started); hosts should exclude them from a */
    ambient?: boolean;
    uuid: UUID;
    session_id: string;
};

type SDKToolProgressMessage = {
    type: 'tool_progress';
    tool_use_id: string;
    tool_name: string;
    parent_tool_use_id: string | null;
    elapsed_time_seconds: number;
    task_id?: string;
    uuid: UUID;
    session_id: string;
    heartbeat?: boolean;
    subagent_type?: string;
    subagent_retry?: {
        agent_id: string;
        attempt: number;
        max_retries: number;
        retry_delay_ms: number;
        error_status: number | null;
        error_category: string;
    };
};

type SDKToolUseSummaryMessage = {
    type: 'tool_use_summary';
    summary: string;
    preceding_tool_use_ids: string[];
    uuid: UUID;
    session_id: string;
};

type SDKPermissionDeniedMessage = {
    type: 'system';
    subtype: 'permission_denied';
    tool_name: string;
    tool_use_id: string;
    /** * Subagent ID when the denied tool call originated inside a subagent. Mirrors can_use_tool for host-side routing. */
    agent_id?: string;
    /** * Discriminator from PermissionDecisionReason (e.g. 'classifier', 'asyncAgent', 'mode', 'rule'). */
    decision_reason_type?: string;
    /** * Human-readable reason from the deciding component, when available. */
    decision_reason?: string;
    /** * The rejection message returned to the model in the tool_result. */
    message: string;
    uuid: UUID;
    session_id: string;
};

type SDKPermissionDenial = {
    tool_name: string;
    tool_use_id: string;
    tool_input: Record<string, unknown>;
};

type SDKRateLimitEvent = {
    type: 'rate_limit_event';
    /** * Rate limit information for claude.ai subscription users. */
    rate_limit_info: SDKRateLimitInfo;
    uuid: UUID;
    session_id: string;
};

type SDKRateLimitInfo = {
    status: 'allowed' | 'allowed_warning' | 'rejected';
    resetsAt?: number;
    rateLimitType?: 'five_hour' | 'seven_day' | 'seven_day_opus' | 'seven_day_sonnet' | 'seven_day_overage_included' | 'overage';
    utilization?: number;
    overageStatus?: 'allowed' | 'allowed_warning' | 'rejected';
    overageResetsAt?: number;
    overageDisabledReason?: 'overage_not_provisioned' | 'org_level_disabled' | 'org_level_disabled_until' | 'out_of_credits' | 'seat_tier_level_disabled' | 'member_level_disabled' | 'seat_tier_zero_credit_limit' | 'group_zero_credit_limit' | 'member_zero_credit_limit' | 'org_service_level_disabled' | 'no_limits_configured' | 'fetch_error' | 'unknown';
    isUsingOverage?: boolean;
    overageInUse?: boolean;
    surpassedThreshold?: number;
    /** * Which spend limit blocked the request when it is not the member's own cap: 'group_pool' means a pooled group budget shared by the member's team is used up (th */
    limitScope?: 'service' | 'channel' | 'group_pool';
    errorCode?: 'credits_required';
    canUserPurchaseCredits?: boolean;
    hasChargeableSavedPaymentMethod?: boolean;
};

type SDKInformationalMessage = {
    type: 'system';
    subtype: 'informational';
    content: string;
    /** * Render level. 'info' shows only in transcript mode; 'notice' renders in inactive gray; 'suggestion' and 'warning' are more prominent. */
    level: 'info' | 'notice' | 'suggestion' | 'warning';
    /** * Dedupes progress messages for the same tool use. */
    tool_use_id?: string;
    /** * When true, execution stops after this message (e.g. a Stop hook denied continuation). */
    prevent_continuation?: boolean;
    uuid: UUID;
    session_id: string;
};

type SDKNotificationMessage = {
    type: 'system';
    subtype: 'notification';
    key: string;
    text: string;
    priority: 'low' | 'medium' | 'high' | 'immediate';
    color?: string;
    timeout_ms?: number;
    uuid: UUID;
    session_id: string;
};

type SDKBackgroundTasksChangedMessage = {
    type: 'system';
    subtype: 'background_tasks_changed';
    /** * Every live background task after the change. REPLACE semantics: swap your set for this payload. */
    tasks: {
        task_id: string;
        task_type: string;
        description: string;
        /** * True for tasks that are not activity (every skip_transcript task, plus every live-update watcher, requested or auto-started); hosts should exclude them from a */
        ambient?: boolean;
    }[];
    uuid: UUID;
    session_id: string;
};

type SDKSessionStateChangedMessage = {
    type: 'system';
    subtype: 'session_state_changed';
    state: 'idle' | 'running' | 'requires_action';
    uuid: UUID;
    session_id: string;
};

type SDKModelRefusalFallbackMessage = {
    type: 'system';
    subtype: 'model_refusal_fallback';
    trigger: 'refusal';
    direction: 'retry' | 'revert' | 'sticky';
    /** * 'session': the main thread fell back and the session model is swapped. 'local': a subagent / side-question (/btw) / background fork fell back — only that resp */
    scope?: 'session' | 'local';
    original_model: string;
    fallback_model: string;
    request_id: string | null;
    /** * The refusal category ('cyber', 'bio', …): stop_details.category from the refused API response (client lane), or the fallback block's server-gated trigger.cate */
    api_refusal_category?: string | null;
    /** * stop_details.explanation from the refused API response (client lane only — the server-lane trigger carries no explanation). Unstable human prose — display onl */
    api_refusal_explanation?: string | null;
    /** * Wire uuids of the messages this fallback retracted — the refused partial as the consumer received it (one uuid per normalized SDK message; multi-block message */
    retracted_message_uuids?: string[];
    /** * UUID of the user message the refused request was for — the rewind target and composer prefill for edit-and-retry. This is the message's own uuid as delivered  */
    refused_user_message_uuid?: string | null;
    content: string;
    uuid: UUID;
    session_id: string;
};

type SDKModelRefusalNoFallbackMessage = {
    type: 'system';
    subtype: 'model_refusal_no_fallback';
    original_model: string;
    request_id: string | null;
    api_refusal_category?: string | null;
    api_refusal_explanation?: string | null;
    refused_user_message_uuid?: string | null;
    content: string;
    uuid: UUID;
    session_id: string;
};

type SDKPluginInstallMessage = {
    type: 'system';
    subtype: 'plugin_install';
    status: 'started' | 'installed' | 'failed' | 'completed';
    name?: string;
    error?: string;
    uuid: UUID;
    session_id: string;
};

type SDKLocalCommandOutputMessage = {
    type: 'system';
    subtype: 'local_command_output';
    content: string;
    uuid: UUID;
    session_id: string;
};

type SDKMemoryRecallMessage = {
    type: 'system';
    subtype: 'memory_recall';
    /** * How memories were surfaced: 'select' returns full file bodies chosen by the parallel selector; 'synthesize' returns a Sonnet-authored paragraph distilled from */
    mode: 'select' | 'synthesize';
    memories: {
        /** * Absolute path to the memory file, a synthesis sentinel of the form `<synthesis:DIR>` when mode is 'synthesize', or an https URL when scope is 'organization'. */
        path: string;
        scope: 'personal' | 'team' | 'organization';
        /** * The surfaced memory body. Always present for 'synthesize' mode and 'organization' scope (neither has an on-disk path to lazy-load from); absent for file-backe */
        content?: string;
    }[];
    uuid: UUID;
    session_id: string;
};

type SDKFilesPersistedEvent = {
    type: 'system';
    subtype: 'files_persisted';
    files: {
        filename: string;
        file_id: string;
    }[];
    failed: {
        filename: string;
        error: string;
    }[];
    processed_at: string;
    uuid: UUID;
    session_id: string;
};

type SDKElicitationCompleteMessage = {
    type: 'system';
    subtype: 'elicitation_complete';
    mcp_server_name: string;
    elicitation_id: string;
    uuid: UUID;
    session_id: string;
};

type SDKPromptSuggestionMessage = {
    type: 'prompt_suggestion';
    suggestion: string;
    uuid: UUID;
    session_id: string;
};

type SDKConversationResetMessage = {
    type: 'conversation_reset';
    new_conversation_id: UUID;
    uuid: UUID;
    session_id: string;
};

type SDKWorkerShuttingDownMessage = {
    type: 'system';
    subtype: 'worker_shutting_down';
    /** * Short snake_case reason set by the host CLI (not user input), e.g. 'host_exit', 'remote_control_disabled'. */
    reason: string;
    uuid: UUID;
    session_id: string;
};

type SDKMirrorErrorMessage = {
    type: 'system';
    subtype: 'mirror_error';
    error: string;
    key: {
        projectKey: string;
        sessionId: string;
        subpath?: string;
    };
    uuid: UUID;
    session_id: string;
};

type SDKCommandsChangedMessage = {
    type: 'system';
    subtype: 'commands_changed';
    commands: SlashCommand[];
    uuid: UUID;
    session_id: string;
};

type SDKControlRequestProgressMessage = {
    type: 'system';
    subtype: 'control_request_progress';
    /** * request_id of the in-flight control_request this progress belongs to. */
    request_id: string;
    status: 'started' | 'api_retry';
    attempt?: number;
    max_retries?: number;
    retry_delay_ms?: number;
    error_status?: number | null;
    uuid: UUID;
    session_id: string;
};

type SDKAuthStatusMessage = {
    type: 'auth_status';
    isAuthenticating: boolean;
    output: string[];
    error?: string;
    uuid: UUID;
    session_id: string;
};

type TerminalReason = 'blocking_limit' | 'rapid_refill_breaker' | 'prompt_too_long' | 'image_error' | 'model_error' | 'api_error' | 'malformed_tool_use_exhausted' | 'aborted_streaming' | 'aborted_tools' | 'stop_hook_prevented' | 'hook_stopped' | 'tool_deferred' | 'max_turns' | 'background_requested' | 'completed' | 'budget_exhausted' | 'structured_output_retry_exhausted' | 'tool_deferred_unavailable' | 'turn_setup_failed';

type SDKDeferredToolUse = {
    id: string;
    name: string;
    input: Record<string, unknown>;
};
```
