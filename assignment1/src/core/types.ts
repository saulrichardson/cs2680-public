export type RunStatus = 'running' | 'finished' | 'failed' | 'cancelled';

/**
 * What made a run, and therefore what it is evidence of.
 *
 * - `execution` — a `claude` child process ran in this lane's directory. It is the only kind that
 *   changed files, cost money, holds a session a follow-up can resume, and can stand as a race
 *   candidate.
 * - `replay` — a stored run re-emitted through the same channel for viewing. It changes nothing.
 * - `import` — a recording from somewhere else, rendered as a run. Its numbers describe whatever
 *   machine produced it, so they must never be attributed to this lane's worktree.
 *
 * Everything that asks "did this run actually do the work?" asks this, rather than inferring it
 * from a null session id or the newest row in the lane.
 */
export type RunOrigin = 'execution' | 'replay' | 'import';

/** Playback of something already recorded: neither kind executes anything. */
export function isPlayback(origin: RunOrigin): boolean {
  return origin !== 'execution';
}
export type CallStatus = 'pending' | 'done' | 'error';
export type ToolClass = 'search' | 'mutate' | 'execute' | 'delegate' | 'network' | 'other';
export type PermissionStrategy = 'allowlist' | 'bypass';

/**
 * Whether a run was started with the Claude in Chrome extension enabled (`--chrome`).
 *
 * A lane setting *and* a run column, like `permission`: what a session is configured to do next and
 * what a particular run was actually started with are different facts, and only the second explains
 * a trajectory. Neither is evidence that the browser answered — see `BrowserStatus`.
 */
export type BrowserMode = 'off' | 'chrome';

/** The MCP server the Chrome extension is exposed as; its tools are `mcp__<this>__<tool>`. */
export const CHROME_MCP_SERVER = 'claude-in-chrome';
export const CHROME_TOOL_PREFIX = `mcp__${CHROME_MCP_SERVER}__`;

/**
 * What is actually known about the browser in one run, in increasing order of evidence.
 *
 * The distinctions this type exists to keep are between *asking*, *being able to*, *trying* and
 * *succeeding*. `--chrome` on the command line only says what was requested; the CLI starts
 * normally when Chrome is not running; and a disconnected extension still answers a tool call, with
 * an error. So:
 *
 * - `off` — not requested, and nothing browser-shaped happened. Nothing to say.
 * - `requested` — `--chrome` was passed and the run has not reported its setup yet.
 * - `unavailable` — the run's `system/init` arrived and carried no browser tools, so no browser
 *   action is possible in it at all.
 * - `loaded` — init listed the `claude-in-chrome` tools, so the CLI loaded the MCP server. This is
 *   *not* a connected browser: a disconnected extension still answers a call, with an error.
 * - `calling` — a browser call is in flight and nothing has come back yet. Waiting is not failing,
 *   and the first call of a healthy run sits here for a second or two.
 * - `attempted` — a browser call has actually come back an error and none has succeeded. What a
 *   disconnected extension looks like from here, and deliberately not called use.
 * - `used` — a browser call succeeded. The only value that is evidence of real browser activity,
 *   counted from the trajectory rather than claimed.
 *
 * `calling` and `attempted` are separate because merging them made the first in-flight call of
 * every healthy Chrome run announce "calls failing" over a banner reading "0 browser calls failed".
 */
export type BrowserStatus = 'off' | 'requested' | 'unavailable' | 'loaded' | 'calling' | 'attempted' | 'used';

export interface BrowserView {
  status: BrowserStatus;
  /** Browser tool calls this run made, including ones its delegates made. */
  callCount: number;
  /** Of those, how many came back without an error — the ones that are evidence. */
  successCount: number;
  /** Of those, how many the tool answered with an error — a disconnect, a refused site. */
  errorCount: number;
  /** Of those, how many have not come back at all yet. Waiting, not failing. */
  pendingCount: number;
  /** What the `claude-in-chrome` MCP server reported in `system/init`, when it was listed. */
  serverStatus?: string;
  /** The most recent failure's own words, bounded, for a status line that can explain itself. */
  lastError?: string;
}

/** One raw stream-json line, parsed. Unknown fields are preserved. */
export interface RawEvent {
  type: string;
  subtype?: string;
  uuid?: string;
  session_id?: string;
  parent_tool_use_id?: string | null;
  timestamp?: string;
  [key: string]: unknown;
}

export interface Envelope {
  laneId: string;
  runId: string;
  seq: number;
  receivedAt: number;
  event: RawEvent;
}

export interface RunError {
  message: string;
  exitCode?: number | null;
  signal?: string | null;
  stderrTail?: string;
}

/**
 * What the CLI's `result` event reported about a turn.
 *
 * Every figure is optional, and `undefined` means *the run did not report it* — which is not the
 * same as zero. Defaulting an absent `total_cost_usd` to `0` made an unreported cost indistinguishable
 * from a free run, and in a race that is the difference between "we do not know what this cost" and
 * "this was the cheapest candidate". A reported `0` is still a real, displayable zero.
 */
export interface RunNumbers {
  costUsd?: number;
  durationMs?: number;
  durationApiMs?: number;
  numTurns?: number;
}

/**
 * How many of a candidate's attempts did not report each figure. All zero means the totals beside
 * them really are totals; anything above zero makes that figure a lower bound.
 */
export interface MetricGaps {
  costUsd: number;
  durationMs: number;
  numTurns: number;
}

/** What the CLI's `result` event claimed about the turn: its `subtype` and whether it flagged an error. */
export interface RunOutcome {
  subtype: string;
  isError: boolean;
}

export interface RunLifecycle {
  laneId: string;
  runId: string;
  status: RunStatus;
  /** What made this run (see `RunOrigin`). The server always knows; it is never inferred here. */
  origin: RunOrigin;
  startedAt: number;
  endedAt?: number;
  sessionId?: string;
  numbers?: RunNumbers;
  error?: RunError;
  /**
   * This run is one the lane could actually resume: it ran here (not a replay, not an import) and
   * got far enough to have a session with turns in it. Omitted while the run is still starting.
   */
  resumable?: boolean;
}

export type WsServerMessage =
  | { kind: 'hello'; serverTime: number }
  /** Session inventory changed; every tab should refetch its lists and open-session metadata. */
  | { kind: 'lanes-changed' }
  | { kind: 'events'; envelopes: Envelope[] }
  | { kind: 'run'; lifecycle: RunLifecycle }
  /**
   * What the server knows about a lane right now: every run it holds, with its current lifecycle.
   * Sent when a client subscribes, which is also when it reconnects — so a client that was away
   * while a run ended, or while a whole run came and went, learns about it from the authority
   * rather than waiting for an event that will never come (readiness review R1).
   */
  | { kind: 'lane-state'; laneId: string; runs: RunLifecycle[] }
  | { kind: 'subscribed'; laneIds: string[] }
  | { kind: 'unsubscribed'; laneIds: string[] };

export type WsClientMessage =
  | { kind: 'subscribe'; laneIds: string[]; resume: Record<string, number> }
  | { kind: 'unsubscribe'; laneIds: string[] };

// ---------- wire shapes ----------
// What the tRPC procedures hand the browser. They live here, with the rest of the shared
// vocabulary, so the client can name them without importing the server's router module.

export interface LaneDto {
  id: string;
  name: string;
  /** Where this session's agent runs; inside its own checkout when the session is isolated. */
  cwd: string;
  /** The folder that groups this session with its siblings. See `lanes.project_root`. */
  projectRoot: string;
  permission: string;
  groupId: string | null;
  groupIndex: number | null;
  createdAt: number;
  /** Newest run's end (or start), so lists can order by activity rather than by creation. */
  lastActivityAt: number;
  /** Set once the operator archived it. Files and history are kept; `lanes.reopen` undoes it. */
  archivedAt: number | null;
  /** This session works in a checkout this console created, so its files are its own. */
  isolated: boolean;
}

export interface RunSummaryDto extends RunLifecycle {
  prompt: string;
  cwd: string;
  resumedFrom?: string;
  replayOf?: string;
  model?: string;
  groupId?: string;
  /** What this run was started with (`runs.browser`). */
  browser: BrowserMode;
  /**
   * Always present here (unlike on a lifecycle published mid-start): whether a follow-up in this
   * lane would resume this run, so the composer can offer "Run" instead of "Follow up" when it
   * would not. See `repo.isResumeCandidate`.
   */
  resumable: boolean;
}

// ---------- view model ----------

export interface Hunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: string[]; // each prefixed with ' ', '+', or '-'
}

export interface Patch {
  filePath: string;
  hunks: Hunk[];
}

export interface ToolResult {
  text: string;
  isError: boolean;
  ts: number;
  structured?: unknown; // tool_use_result, per-tool shape
  /**
   * Harness bookkeeping rather than the tool's own output — the "Async agent launched successfully"
   * blurb a delegate call gets the instant the subagent is spawned. The card hides such text and
   * waits for the task's real report (see `TaskInfo.summary`).
   */
  internal?: boolean;
}

export interface TaskInfo {
  taskId?: string;
  /** The delegate's own one-line brief, from `task_started` (or the Agent call's input). */
  description?: string;
  /** What the delegate is doing right now, from `task_progress`. Never overwrites `description`. */
  activity?: string;
  subagentType?: string;
  status?: 'running' | 'completed' | 'failed' | 'stopped' | 'killed' | 'paused' | 'pending';
  totalTokens?: number;
  toolUses?: number;
  durationMs?: number;
  lastToolName?: string;
  summary?: string;
  spawnDepth?: number;
  /** The Agent tool returned immediately (`isAsync`/`status: 'async_launched'`); the task runs on. */
  async?: boolean;
  completedAt?: number;
}

export interface ToolCall {
  id: string;
  name: string;
  toolClass: ToolClass;
  input: Record<string, unknown>;
  ts: number;
  messageId?: string;
  status: CallStatus;
  result?: ToolResult;
  durationMs?: number;
  parentToolUseId: string | null;
  children: Block[];
  task?: TaskInfo;
  patches: Patch[];
  elapsedSeconds?: number; // from tool_progress heartbeats while pending
}

// Every member of `Block` below is exported, including the ones no other module imports today.
// This file's whole job is to name the shared vocabulary: a union whose members are half nameable
// and half not — decided by which call site happens to annotate a variable this week — is worse
// than one that is uniformly open. Same for `SetupInfo` and `RateLimitView` on `RunView`.
export interface TextBlock {
  kind: 'text';
  id: string;
  markdown: string;
  ts: number;
  messageId?: string;
}
export interface ThinkingBlock {
  kind: 'thinking';
  id: string;
  ts: number;
  messageId?: string;
}
export interface ToolBlock {
  kind: 'tool';
  callId: string;
}
export interface SubagentPromptBlock {
  kind: 'subagent-prompt';
  id: string;
  text: string;
  ts: number;
}
export interface NoticeBlock {
  kind: 'notice';
  id: string;
  level: 'info' | 'warning' | 'error';
  title: string;
  text?: string;
  ts: number;
  /** `interrupted`: the CLI's `[Request interrupted by user]` marker — a quiet "Stopped by you" line. */
  variant?: 'interrupted';
}
export interface UnparsedBlock {
  kind: 'unparsed';
  id: string;
  raw: string;
  error: string;
  ts: number;
}
export type Block = TextBlock | ThinkingBlock | ToolBlock | SubagentPromptBlock | NoticeBlock | UnparsedBlock;

export interface ModelUsageView {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  thinkingTokens?: number;
  costUsd: number;
  contextWindow?: number;
}

export interface RunUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  thinkingTokens?: number;
  perModel: Record<string, ModelUsageView>;
}

export interface HookInfo {
  id: string;
  name: string;
  event: string;
  outcome?: string;
  exitCode?: number;
}

export interface McpServerInfo {
  name: string;
  /** Whatever the CLI reported — `connected`, `failed`, `needs-auth`, … Never normalised. */
  status?: string;
}

export interface SetupInfo {
  claudeCodeVersion?: string;
  permissionMode?: string;
  toolCount?: number;
  hooks: HookInfo[];
  /** MCP servers `system/init` listed, verbatim. Empty array means it listed none. */
  mcpServers: McpServerInfo[];
  /**
   * Browser tool names this run was started with, from `system/init`'s tool list.
   *
   * The honest answer to "is Chrome actually on in this run?": the CLI only publishes these once it
   * has connected to the extension, so their presence is the run's own testimony rather than a
   * re-reading of the flag we passed.
   */
  browserTools: string[];
}

export interface RateLimitView {
  status: string;
  type?: string;
  utilization?: number;
  resetsAt?: number;
}

export interface RunView {
  runId: string;
  laneId: string;
  prompt: string;
  cwd: string;
  status: RunStatus;
  /** What made this run (see `RunOrigin`); `execution` until the server says otherwise. */
  origin: RunOrigin;
  startedAt: number;
  endedAt?: number;
  sessionId?: string;
  model?: string;
  resumedFrom?: string;
  replayOf?: string;
  /** See `RunLifecycle.resumable`: whether a follow-up in this lane would resume this run. */
  resumable?: boolean;
  /** What this run was *started* with. The server sets it; the events never carry it. */
  browser: BrowserMode;
  blocks: Block[];
  callsById: Record<string, ToolCall>;
  numbers?: RunNumbers;
  /** The `result` event's claim, kept as a fact and never promoted to `status`: only the server's
   *  lifecycle sets a run's status (`applyLifecycle`), live and after a reload alike. */
  outcome?: RunOutcome;
  usage?: RunUsage;
  /** Live: tokens the model is currently carrying (input + cache read + cache creation of the latest main-thread message). */
  context: { tokens: number; messageId?: string };
  thinking?: { estimatedTokens: number; since: number };
  activity?: string;
  summary?: { category: string; detail: string };
  setup: SetupInfo;
  rateLimit?: RateLimitView;
  retries: number;
  deniedCount: number;
  error?: RunError;
  unparsedCount: number;
  ignoredCount: number;
  eventCount: number;
  lastSeq: number;
}

// ---------- the session's agent graph ----------
// A session is one conversation; a delegation the model makes inside it is a *node* of that
// conversation, not a session of its own. These types name what the console can honestly say about
// such a node, which is exactly what the CLI reports about the task: its brief, its lifecycle, the
// activity it forwards, the figures it publishes, and its written report. Nothing here is inferred
// from prose, and nothing is invented for a node the CLI has not described.

/**
 * Where a delegate is in its life, from the task events the CLI actually sent.
 *
 * `launching` is the gap between the `Agent` tool call appearing in the stream and anything coming
 * back about it — no launch receipt, no `task_started`. The delegation has been asked for and
 * nothing has confirmed it. The receipt is the confirmation, and it is why `working` begins there
 * rather than at `task_started`: `{ isAsync: true }` means the subagent is away and running, and
 * the reducer records the task as `running` from that moment.
 *
 * None of the three non-terminal states is a claim about how the delegate *ended*; see
 * `AgentNode.unresolved` for the case where the run stopped before it said.
 */
export type AgentNodeState = 'launching' | 'working' | 'paused' | 'completed' | 'failed' | 'stopped';

/** Task statuses the CLI does not revise; `deriveAgentGraph` maps them straight through. */
export const AGENT_TERMINAL_STATES: ReadonlySet<AgentNodeState> = new Set<AgentNodeState>([
  'completed',
  'failed',
  'stopped',
]);

/**
 * Figures a delegate published about itself, and only those.
 *
 * Every field is optional and `undefined` means *the task did not report it*. A subagent's cost and
 * turn count are not among them: the CLI reports neither per task, and adding the parent's figures
 * here would attribute the whole session's spend to one child.
 */
export interface AgentReported {
  totalTokens?: number;
  toolUses?: number;
  durationMs?: number;
}

/** One delegate in a session: an `Agent`/`Task` call, described by its own task lifecycle. */
export interface AgentNode {
  /** `runId~callId` (see `callKey`). Both halves: a replay re-uses the recording's call ids. */
  key: string;
  runId: string;
  callId: string;
  /** The delegate that spawned this one, or `null` when the main thread did. */
  parentKey: string | null;
  /** Nested delegates, oldest first. */
  childKeys: string[];
  /** 1 for a delegation made by the main thread, 2 for one made by a delegate, and so on. */
  depth: number;
  /**
   * What produced the run this delegate belongs to.
   *
   * On the node because a session can hold a live run *and* a replay of it, whose delegates carry
   * the same CLI call ids and the same titles. They are distinct nodes, but that is only true in
   * the DOM unless the view can also say which is which — so the provenance travels with the node.
   */
  origin: RunOrigin;
  /** What it was asked to do, in a line: `task_started`'s description, else the call's own. */
  title: string;
  /** The full brief the `Agent` call carried, when it carried one. */
  assignment?: string;
  subagentType?: string;
  state: AgentNodeState;
  /**
   * What it is doing *right now* (`task_progress`). Present only while it is still going, so a
   * finished or interrupted delegate never shows a line that reads as present tense.
   */
  activity?: string;
  /**
   * The last thing it reported doing, whether or not it is still going.
   *
   * Kept separately because a delegate whose run was stopped mid-task has no outcome but does have
   * evidence, and throwing away the final progress line would discard the most useful thing anyone
   * could know about where it got to. The view labels it as the last report, not as current work.
   */
  lastActivity?: string;
  lastToolName?: string;
  reported: AgentReported;
  /** Its written report (`task_notification.summary`, or a synchronous result's text). */
  report?: string;
  /** Whether `report` should be rendered as markdown — true only for the task's own summary. */
  reportIsMarkdown: boolean;
  /** What the CLI forwarded of its trajectory. Empty without `--forward-subagent-text`. */
  blocks: Block[];
  /** Browser tool calls this delegate made itself — the evidence that a child drove Chrome. */
  browserCalls: number;
  /**
   * The run ended before this delegate reported a terminal status, so how it finished is not known.
   *
   * `state` still holds the last thing the task said about itself, because that is the evidence the
   * run left behind. What must not happen is either lie: going on claiming a delegate is at work
   * minutes after its session was stopped, or promoting an unfinished task to `completed` because
   * the parent finished. This flag is how the view says "it was working, and then the run ended".
   */
  unresolved: boolean;
  startedAt: number;
  endedAt?: number;
}

/**
 * Every delegate a session has revealed so far, and how they connect.
 *
 * Derived from the same `RunView`s the trajectory renders, so a live run and a replay of it produce
 * the same graph, and a replay reveals each node exactly when its recorded events are reached.
 */
export interface AgentGraph {
  /** Depth-first, in declaration order: a parent always precedes its own children. */
  nodes: AgentNode[];
  byKey: Record<string, AgentNode>;
  /** Delegations made by a main thread, oldest first. */
  rootKeys: string[];
  /** How many nodes are in each state — what the session's own chip reports. */
  tally: Record<AgentNodeState, number>;
  /**
   * Delegates that are genuinely still going: non-terminal *and* in a run that has not ended.
   *
   * Not the sum of the non-terminal tallies, which is the bug this replaces. A cancelled run leaves
   * its children sitting at `working`, and adding those up told the operator "1 active" about a
   * session where nothing had been running for an hour.
   */
  activeCount: number;
  /** Of those, how many never reported an outcome before their run ended (`AgentNode.unresolved`). */
  unresolvedCount: number;
  /** Browser tool calls made by delegates in this session. */
  browserCalls: number;
}

export interface OutlineItem {
  callId: string;
  name: string;
  toolClass: ToolClass;
  status: CallStatus;
  depth: number;
  summary: string;
}

export interface TimelineBar {
  callId: string;
  name: string;
  toolClass: ToolClass;
  status: CallStatus;
  depth: number;
  start: number;
  end: number; // for pending calls: the `now` passed to deriveTimeline
  parentCallId: string | null;
}

export interface RunSummary {
  callCount: number;
  callsByClass: Record<ToolClass, number>;
  errorCount: number;
  subagentCount: number;
  filesTouched: string[];
  linesAdded: number;
  linesRemoved: number;
  textBlocks: number;
}

export interface FileStat {
  path: string;
  added: number;
  removed: number;
  binary: boolean;
  /** Set when the numstat entry reported a rename ("old => new" or "prefix/{old => new}/suffix"). */
  renamedFrom?: string;
}

/** One execution that contributed to a lane's candidate: its first attempt, or a follow-up. */
export interface CandidateAttempt {
  runId: string;
  prompt: string;
  status: RunStatus;
  startedAt: number;
  endedAt?: number;
  numbers?: RunNumbers;
}

/**
 * What one lane is offering the operator to choose between.
 *
 * A candidate is the *work in this lane's worktree*, not the newest row in its run list. Only
 * executions count: a replay or an import in a racing lane is a view of work done elsewhere, and
 * letting one stand in for the candidate is how a read-only replay used to take the kept badge off
 * a race result (readiness review R3). Where several executions contributed — a first attempt and
 * follow-ups — the figures are totals across all of them, because the diff spans all of them too,
 * while the status is the latest execution's, because that is the state the lane is in.
 */
export interface CompareLane {
  laneId: string;
  /** This lane's position in the race, from `lanes.group_index` — what gives it its identity hue. */
  groupIndex: number;
  name: string;
  /** The latest execution in this lane; `null` when it has never executed anything. */
  runId: string | null;
  /** The latest execution's status: what this lane is doing, or how it finished. */
  status: RunStatus | 'none';
  /** Every execution in this lane, oldest first. One entry is the ordinary case. */
  attempts: CandidateAttempt[];
  /** Totals across `attempts` (see `aggregateNumbers`). */
  numbers?: RunNumbers;
  /**
   * Per figure, how many attempts did not report it — a run still going, one that ended without a
   * result event, or one whose result omitted that field.
   *
   * It exists because the alternative is silent understatement. Summing what is present and
   * presenting it as the total let a lane whose second attempt crashed look like the cheapest
   * candidate in the race, which is exactly the judgement the operator is here to make.
   */
  metricGaps: MetricGaps;
  /**
   * Total agent wall time across `attempts` — the sum of each attempt's own span, not the span
   * from first start to last end. The difference is the time the operator spent reading the first
   * result before typing the follow-up, which the agent did not spend. (It does include the
   * agent's own thinking time, which is work.)
   */
  wallMs?: number;
  /** Totals across `attempts` (see `aggregateSummaries`). */
  summary?: RunSummary;
  worktreePath: string;
  branch: string;
  files: FileStat[];
  patches: Patch[];
  error?: string;
}

export interface CompareResult {
  groupId: string;
  prompt: string;
  repoRoot: string;
  baseCommit: string;
  /** The execution the operator chose. Never a replay or an import (`fanout.keep` refuses those). */
  keptRunId: string | null;
  /**
   * The kept execution is no longer the latest one in its lane: a follow-up has since changed that
   * worktree, so the choice was made about a state that no longer exists. Saying so is more honest
   * than moving the badge, which would claim the operator approved work they never saw.
   */
  keptSuperseded: boolean;
  lanes: CompareLane[];
}
