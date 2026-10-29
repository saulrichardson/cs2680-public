import { isBrowserTool } from './classify';
import { walkCalls } from './derive';
import { callKey } from './ref';
import { TERMINAL_STATUSES } from './status';
import {
  AGENT_TERMINAL_STATES,
  type AgentGraph,
  type AgentNode,
  type AgentNodeState,
  type AgentReported,
  type RunView,
  type TaskInfo,
  type ToolCall,
} from './types';

/**
 * The delegates a session has revealed, and how they connect.
 *
 * This is a projection of the same `RunView`s the trajectory renders — no separate store, no
 * planner, no events invented for the view. Three consequences are the whole point:
 *
 * - a replay produces the graph the live run produced, node for node, and reveals each node at the
 *   moment its recorded `Agent` call is reached rather than all at once at the start;
 * - a node is named by run *and* call, so replaying a recording into the lane it came from gives two
 *   distinct nodes rather than one that flickers between two trajectories;
 * - a node says only what the CLI said about that task. There is no per-task cost or turn count in
 *   the stream, so there is none here.
 *
 * The runs are a whole session's: the first prompt and every follow-up. Delegations from different
 * runs are all roots of the same session, in the order they were declared, because that is the order
 * the operator watched them appear in.
 */
export function deriveAgentGraph(runs: RunView[]): AgentGraph {
  const nodes: AgentNode[] = [];
  const byKey: Record<string, AgentNode> = {};
  const rootKeys: string[] = [];
  for (const run of runs) {
    // `walkCalls` is depth-first over the run's own block tree, so a delegate is always visited
    // before anything it spawned: `parentKey` below can rely on its parent already existing.
    for (const { call } of walkCalls(run, run.blocks, 0, null)) {
      if (call.toolClass !== 'delegate') continue;
      const parentKey = nearestDelegateAncestor(run, call, byKey);
      const parent = parentKey === null ? undefined : byKey[parentKey];
      const node = buildNode(run, call, parent);
      byKey[node.key] = node;
      nodes.push(node);
      if (parent) parent.childKeys.push(node.key);
      else rootKeys.push(node.key);
    }
  }
  const tally = emptyTally();
  let unresolvedCount = 0;
  let activeCount = 0;
  let browserCalls = 0;
  for (const n of nodes) {
    tally[n.state] += 1;
    if (n.unresolved) unresolvedCount += 1;
    // Still going means non-terminal *and* not stranded by a run that ended. A delegate whose run
    // was stopped keeps `working` as its last reported state, and counting that as active is how a
    // finished session goes on claiming somebody is at work in it.
    else if (!AGENT_TERMINAL_STATES.has(n.state)) activeCount += 1;
    browserCalls += n.browserCalls;
  }
  return { nodes, byKey, rootKeys, tally, activeCount, unresolvedCount, browserCalls };
}

/**
 * A node by key, asking about *own* membership.
 *
 * `byKey` is a plain object, so a bare lookup would also find everything on `Object.prototype`. A
 * key is `runId~callId` and both halves come from a recording that may be malformed or adversarial.
 */
export function agentNode(graph: AgentGraph, key: string | null | undefined): AgentNode | undefined {
  if (!key) return undefined;
  return Object.hasOwn(graph.byKey, key) ? graph.byKey[key] : undefined;
}

/** The chain from the session root down to `key`, outermost first, or `[]` for an unknown key. */
export function agentPath(graph: AgentGraph, key: string): AgentNode[] {
  const path: AgentNode[] = [];
  let node = agentNode(graph, key);
  // `nodes.length` is a hard stop: a graph assembled some other way could in principle cycle, and
  // walking it forever is a poor way to find out.
  for (let i = 0; node && i <= graph.nodes.length; i += 1) {
    path.unshift(node);
    node = agentNode(graph, node.parentKey);
  }
  return path;
}

// ---------- internals ----------

function emptyTally(): Record<AgentNodeState, number> {
  return { launching: 0, working: 0, paused: 0, completed: 0, failed: 0, stopped: 0 };
}

/**
 * The delegate that spawned this one, if any.
 *
 * A subagent's own events carry the `Agent` call's id as `parent_tool_use_id`, so a nested
 * delegation's immediate parent is normally the outer delegate itself. The walk up the chain is
 * there for the cases where it is not: an intermediate call the reducer could not resolve, or a
 * trajectory whose ancestry the recording spliced together. It stops at the first ancestor that is
 * a delegate this graph already holds; anything else makes the node a root, which is where the
 * reducer puts an orphaned call too.
 */
function nearestDelegateAncestor(
  run: RunView,
  call: ToolCall,
  byKey: Record<string, AgentNode>,
): string | null {
  let parentId = call.parentToolUseId;
  const seen = new Set<string>([call.id]);
  while (parentId !== null && !seen.has(parentId)) {
    seen.add(parentId);
    const key = callKey({ runId: run.runId, callId: parentId });
    if (Object.hasOwn(byKey, key)) return key;
    const parent = Object.hasOwn(run.callsById, parentId) ? run.callsById[parentId] : undefined;
    if (!parent) return null;
    parentId = parent.parentToolUseId;
  }
  return null;
}

function buildNode(run: RunView, call: ToolCall, parent: AgentNode | undefined): AgentNode {
  const task = call.task;
  const state = nodeState(call);
  // The run is over and this delegate never said how it ended. `state` keeps the last thing it did
  // say — that is the evidence — and `unresolved` says the evidence stops there.
  const unresolved = TERMINAL_STATUSES.has(run.status) && !AGENT_TERMINAL_STATES.has(state);
  const live = !unresolved && !AGENT_TERMINAL_STATES.has(state);
  const summary = task?.summary;
  // A synchronous delegate (older CLIs, `fixtures/subagent-forward.jsonl`) has no task summary: its
  // report *is* the tool result. An async launch receipt is not a report — the reducer marks it
  // `internal` precisely so nothing quotes "Async agent launched successfully" as the child's work.
  const settledResult = call.result && !call.result.internal ? call.result : undefined;
  return {
    key: callKey({ runId: run.runId, callId: call.id }),
    runId: run.runId,
    callId: call.id,
    parentKey: parent?.key ?? null,
    childKeys: [],
    // Parents are always built first (the walk is depth-first), so this is exact rather than a
    // second walk up the ancestry.
    depth: (parent?.depth ?? 0) + 1,
    origin: run.origin,
    title: task?.description ?? str(call.input.description) ?? call.name,
    assignment: str(call.input.prompt) ?? str(rec(call.result?.structured)?.prompt),
    subagentType: task?.subagentType,
    state,
    // Present tense only while it is true. A delegate that has finished, or whose run was stopped
    // under it, must not go on displaying "Reading the zoning table" as what it is doing.
    activity: live ? task?.activity : undefined,
    // The same line, kept unconditionally, so an interrupted delegate can still say where it got to.
    lastActivity: task?.activity,
    lastToolName: task?.lastToolName,
    reported: reportedOf(task),
    report: summary ?? settledResult?.text,
    reportIsMarkdown: summary !== undefined,
    blocks: call.children,
    browserCalls: countBrowserCalls(run, call),
    unresolved,
    startedAt: call.ts,
    endedAt: task?.completedAt ?? settledResult?.ts,
  };
}

/**
 * Browser tool calls this delegate made itself.
 *
 * Only the ones the CLI forwarded can be counted — a subagent's own tool calls arrive with the
 * `Agent` call as their `parent_tool_use_id`, so they are in `children`, but nested delegates'
 * calls belong to *those* nodes and are not counted again here.
 */
function countBrowserCalls(run: RunView, call: ToolCall): number {
  let n = 0;
  for (const block of call.children) {
    if (block.kind !== 'tool') continue;
    const child = Object.hasOwn(run.callsById, block.callId) ? run.callsById[block.callId] : undefined;
    if (child && isBrowserTool(child.name)) n += 1;
  }
  return n;
}

/**
 * What the task events say, mapped to the six states the view can draw.
 *
 * The task's own lifecycle wins wherever it exists, because a delegate's `Agent` call is answered in
 * milliseconds by a launch receipt and would otherwise read as finished (`isAsyncLaunch`). Only when
 * there is no task lifecycle at all does the call's own status decide — which is the synchronous
 * shape, where the result really is the outcome.
 */
function nodeState(call: ToolCall): AgentNodeState {
  switch (call.task?.status) {
    case 'completed':
      return 'completed';
    case 'failed':
    case 'killed':
      return 'failed';
    case 'stopped':
      return 'stopped';
    case 'running':
      return 'working';
    case 'paused':
      return 'paused';
    case 'pending':
      return 'launching';
    default:
      break;
  }
  if (call.status === 'error') return 'failed';
  if (call.status === 'done') return 'completed';
  // Declared, or launched and acknowledged, with nothing reported back yet.
  return 'launching';
}

function reportedOf(task: TaskInfo | undefined): AgentReported {
  return { totalTokens: task?.totalTokens, toolUses: task?.toolUses, durationMs: task?.durationMs };
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}
function rec(v: unknown): Record<string, unknown> | undefined {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
}
