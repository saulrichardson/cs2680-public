import { classifyTool } from './classify';
import { extractPatches } from './patches';
import { contentBlocks, parseContentBlock, toolResultText } from './schemas';
import { TERMINAL_STATUSES } from './status';
import {
  type Block,
  type BrowserMode,
  CHROME_TOOL_PREFIX,
  type Envelope,
  type HookInfo,
  type McpServerInfo,
  type ModelUsageView,
  type RawEvent,
  type RunLifecycle,
  type RunNumbers,
  type RunOrigin,
  type RunStatus,
  type RunUsage,
  type RunView,
  type TaskInfo,
  type ToolCall,
} from './types';

export interface CreateRunViewInit {
  runId: string;
  laneId: string;
  prompt: string;
  cwd: string;
  startedAt: number;
  resumedFrom?: string;
  replayOf?: string;
  status?: RunStatus;
  origin?: RunOrigin;
  browser?: BrowserMode;
}

export function createRunView(init: CreateRunViewInit): RunView {
  return {
    runId: init.runId,
    laneId: init.laneId,
    prompt: init.prompt,
    cwd: init.cwd,
    status: init.status ?? 'running',
    // A view built from a bare envelope (a run first seen over the socket) has no provenance yet.
    // `execution` is the assumption that shows the least: it offers no "recording" badge it might
    // have to take back, and the server's lifecycle or DTO corrects it a moment later.
    origin: init.origin ?? 'execution',
    // What the run was started with is the server's to say; a view built from a bare envelope has
    // not been told yet, and `off` is the value that claims nothing.
    browser: init.browser ?? 'off',
    startedAt: init.startedAt,
    resumedFrom: init.resumedFrom,
    replayOf: init.replayOf,
    blocks: [],
    callsById: {},
    context: { tokens: 0 },
    setup: { hooks: [], mcpServers: [], browserTools: [] },
    retries: 0,
    deniedCount: 0,
    unparsedCount: 0,
    ignoredCount: 0,
    eventCount: 0,
    lastSeq: 0,
  };
}

export function applyEnvelopes(view: RunView, envelopes: Envelope[]): RunView {
  let v = view;
  for (const env of envelopes) v = applyEvent(v, env);
  return v;
}

export function applyEvent(view: RunView, env: Envelope): RunView {
  if (env.seq <= view.lastSeq) return view; // duplicate delivery after a reconnect
  const e = env.event;
  /**
   * One clock per run: the envelope's `receivedAt`.
   *
   * Raw events may carry a `timestamp` (the CLI's wall clock *at recording time*), but it is
   * deliberately not used here. Only some event types carry one — `system` and `result` do not —
   * while `RunView.startedAt` and the lifecycle `endedAt` always come from `receivedAt`, so
   * preferring it mixed two clocks in a single run: for a live run they agree to within
   * milliseconds, but for a replay, an import, or a fixture-driven run the tool calls landed hours
   * away from the run's own axis and the compare timeline drew every bar off its track. Replays and
   * imports instead re-stamp `receivedAt` from the new run's start, keeping the recording's relative
   * timing on one clock (see `Replayer` in `src/server/replay.ts`).
   * The recorded `timestamp` stays in the raw event, which is persisted and exported verbatim.
   */
  const ts = env.receivedAt;
  const next: RunView = {
    ...view,
    eventCount: view.eventCount + 1,
    lastSeq: Math.max(view.lastSeq, env.seq),
  };
  switch (e.type) {
    case 'assistant':
      return applyAssistant(next, e, ts);
    case 'user':
      return applyUser(next, e, ts);
    case 'result':
      return applyResult(next, e);
    case 'system':
      return applySystem(next, e, ts);
    case 'rate_limit_event':
      return applyRateLimit(next, e);
    case 'tool_progress':
      return applyToolProgress(next, e);
    case 'unparsed':
      return {
        ...appendBlock(next, null, {
          kind: 'unparsed',
          id: blockId(e, env),
          raw: String(e.raw ?? ''),
          error: String(e.error ?? ''),
          ts,
        }),
        unparsedCount: next.unparsedCount + 1,
      };
    case 'stream_event':
      return next;
    default:
      return { ...next, ignoredCount: next.ignoredCount + 1 };
  }
}

// ---------- helpers ----------

function blockId(e: RawEvent, env: Envelope, suffix = ''): string {
  return `${typeof e.uuid === 'string' ? e.uuid : `${env.runId}:${env.seq}`}${suffix}`;
}

function parentOf(e: RawEvent): string | null {
  return typeof e.parent_tool_use_id === 'string' ? e.parent_tool_use_id : null;
}

/**
 * A call this run really declared, by id.
 *
 * `callsById` is a plain object, so a bare `callsById[id]` lookup also finds everything on
 * `Object.prototype`: an event naming `constructor`, `toString` or `__proto__` as its parent got
 * back a function or the prototype and the reducer tried to read `.children` off it. Tool call ids
 * come from a recording, which may be adversarial or simply malformed, so every lookup asks about
 * *own* membership.
 */
export function getCall(view: RunView, callId: string): ToolCall | undefined {
  return Object.hasOwn(view.callsById, callId) ? view.callsById[callId] : undefined;
}

/** Append a block to the main thread or to a parent call's children (falls back to main thread if the parent is unknown). */
function appendBlock(view: RunView, parentId: string | null, block: Block): RunView {
  const parent = parentId === null ? undefined : getCall(view, parentId);
  if (parentId && parent) {
    return {
      ...view,
      callsById: { ...view.callsById, [parentId]: { ...parent, children: [...parent.children, block] } },
    };
  }
  return { ...view, blocks: [...view.blocks, block] };
}

function updateCall(view: RunView, callId: string, patch: (c: ToolCall) => ToolCall): RunView {
  const existing = getCall(view, callId);
  if (!existing) return view;
  return { ...view, callsById: { ...view.callsById, [callId]: patch(existing) } };
}

/**
 * Would making `childId` a child of `parentId` create a cycle?
 *
 * Ancestry has to stay acyclic because it is a *tree* that recursive readers walk —
 * `deriveOutline`, `summarizeRun`, `callPath`, the row flattener. A recording that declares the
 * same tool call id twice, the second time naming itself as its own parent, produced a block
 * containing itself and blew the stack on the first render. The invariant belongs here, where the
 * graph is built, rather than as a depth limit in each reader.
 */
function wouldCycle(view: RunView, childId: string, parentId: string): boolean {
  if (childId === parentId) return true;
  const seen = new Set<string>([parentId]);
  let ancestor = getCall(view, parentId)?.parentToolUseId ?? null;
  while (ancestor !== null) {
    if (ancestor === childId) return true;
    if (seen.has(ancestor)) return true; // already broken; refuse to add to it
    seen.add(ancestor);
    ancestor = getCall(view, ancestor)?.parentToolUseId ?? null;
  }
  return false;
}

/**
 * The MCP servers `system/init` listed, kept verbatim.
 *
 * The field is an array of objects in the recordings we have; a build that publishes plain names
 * instead still produces usable rows rather than being dropped, because "the Chrome server is
 * listed" is the fact worth keeping either way.
 */
function mcpServersOf(e: RawEvent): McpServerInfo[] {
  if (!Array.isArray(e.mcp_servers)) return [];
  const out: McpServerInfo[] = [];
  for (const raw of e.mcp_servers) {
    if (typeof raw === 'string') out.push({ name: raw });
    else {
      const s = rec(raw);
      const name = str(s?.name);
      if (name) out.push({ name, status: str(s?.status) });
    }
  }
  return out;
}

function findCallByTaskId(view: RunView, taskId: string): ToolCall | undefined {
  return Object.values(view.callsById).find((c) => c.task?.taskId === taskId);
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}
function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}
function rec(v: unknown): Record<string, unknown> | undefined {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
}

const TASK_STATUSES: ReadonlySet<string> = new Set([
  'running',
  'completed',
  'failed',
  'stopped',
  'killed',
  'paused',
  'pending',
]);

/** Validate a raw status string against the closed `TaskInfo['status']` union; anything else is undefined. */
function taskStatus(v: unknown): TaskInfo['status'] | undefined {
  return typeof v === 'string' && TASK_STATUSES.has(v) ? (v as TaskInfo['status']) : undefined;
}

/** Task statuses a delegate never leaves: the Agent call's own card can settle on them. */
const TASK_TERMINAL: ReadonlySet<string> = new Set(['completed', 'failed', 'stopped', 'killed']);

/**
 * Did this Agent `tool_result` only *launch* the subagent? Since CLI 2.1.270 a delegate call returns
 * immediately with `{ isAsync: true, status: 'async_launched' }` and a line of harness bookkeeping,
 * while the task itself keeps running for minutes — so the call stays pending until its
 * `task_notification` (or a terminal `task_updated`) arrives. A synchronous Agent result (older
 * CLIs, `fixtures/subagent-forward.jsonl`) has neither flag and settles the call as before.
 */
function isAsyncLaunch(structured: unknown): boolean {
  const s = rec(structured);
  return s ? s.isAsync === true || s.status === 'async_launched' : false;
}

// ---------- assistant ----------

function applyAssistant(view: RunView, e: RawEvent, ts: number): RunView {
  const message = rec(e.message) ?? {};
  const messageId = str(message.id);
  const parent = parentOf(e);
  let v: RunView = { ...view, thinking: undefined };
  if (!v.model && str(message.model)) v = { ...v, model: str(message.model) };

  if (parent === null) {
    const usage = rec(message.usage);
    if (usage) {
      const tokens =
        (num(usage.input_tokens) ?? 0) +
        (num(usage.cache_read_input_tokens) ?? 0) +
        (num(usage.cache_creation_input_tokens) ?? 0);
      if (tokens > 0) v = { ...v, context: { tokens, messageId } };
    }
  }

  const blocks = contentBlocks(e);
  blocks.forEach((raw, i) => {
    const id = `${typeof e.uuid === 'string' ? e.uuid : `${messageId ?? 'm'}:${ts}`}:${i}`;
    const parsed = parseContentBlock(raw);
    if (parsed?.kind === 'tool_use') {
      const b = parsed.block;
      // A tool call id identifies one call. A stream that declares the same id twice — a
      // recording spliced together, an adversarial import — must not be allowed to replace the
      // first call's input, result and parent with a second declaration's, or to add a second
      // block for it. The first sighting is the call; the repeat is recorded as what it is.
      const existing = getCall(v, b.id);
      if (existing) {
        v = {
          ...appendBlock(v, parent, {
            kind: 'notice',
            id: `${id}:duplicate`,
            level: 'warning',
            title: `Repeated tool call id ${b.id}`,
            text: `This run already declared ${b.id} (${existing.name}); the repeat is not shown as a second call.`,
            ts,
          }),
          ignoredCount: v.ignoredCount + 1,
        };
        return;
      }
      // Ancestry must stay acyclic; a call that names itself (or one of its own descendants) as
      // its parent goes on the main thread instead, where an orphaned call already goes.
      const parentId = parent !== null && !wouldCycle(v, b.id, parent) ? parent : null;
      const call: ToolCall = {
        id: b.id,
        name: b.name,
        toolClass: classifyTool(b.name),
        // `input` is the schema's normalized value, so a block that arrived without one holds `{}`
        // here rather than `undefined` — which is what every reader of `call.input` assumes.
        input: b.input,
        ts,
        messageId,
        status: 'pending',
        parentToolUseId: parentId,
        children: [],
        patches: [],
      };
      v = { ...v, callsById: { ...v.callsById, [b.id]: call } };
      v = appendBlock(v, parentId, { kind: 'tool', callId: b.id });
    } else if (parsed?.kind === 'thinking') {
      v = appendBlock(v, parent, { kind: 'thinking', id, ts, messageId });
    } else if (parsed?.kind === 'text') {
      if (parsed.block.text.trim().length > 0)
        v = appendBlock(v, parent, { kind: 'text', id, markdown: parsed.block.text, ts, messageId });
    } else if (parsed?.kind === 'malformed') {
      v = appendMalformed(v, parent, id, raw, parsed, ts);
    } else {
      v = { ...v, ignoredCount: v.ignoredCount + 1 };
    }
  });
  return v;
}

/**
 * A block of a known type whose contents are not that type. It is shown, not dropped: a `tool_use`
 * with no `id` is a tool call this console cannot render, and a trajectory that quietly omits it
 * reads as an agent that never made the call. The raw block travels with it, so the operator can
 * see exactly what arrived — the stored event line itself is untouched either way.
 */
function appendMalformed(
  view: RunView,
  parent: string | null,
  id: string,
  raw: Record<string, unknown>,
  parsed: { type: string; reason: string },
  ts: number,
): RunView {
  let json: string;
  try {
    json = JSON.stringify(raw);
  } catch {
    json = String(raw);
  }
  return {
    ...appendBlock(view, parent, {
      kind: 'unparsed',
      id: `${id}:malformed`,
      raw: json,
      error: `malformed ${parsed.type} block: ${parsed.reason}`,
      ts,
    }),
    unparsedCount: view.unparsedCount + 1,
  };
}

// ---------- user ----------

/** The CLI writes this as a plain `user` text block when a run is interrupted (our Stop → SIGINT). */
const INTERRUPT_MARKER = '[Request interrupted by user]';

function applyUser(view: RunView, e: RawEvent, ts: number): RunView {
  const parent = parentOf(e);
  let v = view;
  const blocks = contentBlocks(e);
  blocks.forEach((raw, i) => {
    const id = `${typeof e.uuid === 'string' ? e.uuid : `u:${ts}`}:${i}`;
    const parsed = parseContentBlock(raw);
    if (parsed?.kind === 'tool_result') {
      const b = parsed.block;
      const call = getCall(v, b.tool_use_id);
      if (!call) {
        v = appendBlock(v, parent, {
          kind: 'notice',
          id,
          level: 'warning',
          title: 'Result for an unknown tool call',
          text: b.tool_use_id,
          ts,
        });
        return;
      }
      const structured = e.tool_use_result;
      const isError = b.is_error === true;
      const launched = call.toolClass === 'delegate' && isAsyncLaunch(structured);
      v = updateCall(v, call.id, (c) => ({
        ...c,
        // An async launch is not an outcome: leave the call pending (and un-timed) for the task.
        status: launched ? 'pending' : isError ? 'error' : 'done',
        result: {
          text: toolResultText(b.content),
          isError,
          ts,
          structured,
          ...(launched ? { internal: true } : {}),
        },
        durationMs: launched ? c.durationMs : Math.max(0, ts - c.ts),
        patches: extractPatches(structured),
        task: c.toolClass === 'delegate' ? mergeAgentResult(c.task, structured) : c.task,
      }));
    } else if (parsed?.kind === 'text') {
      const b = parsed.block;
      if (parent) v = appendBlock(v, parent, { kind: 'subagent-prompt', id, text: b.text, ts });
      else if (b.text.trim() === INTERRUPT_MARKER)
        // The CLI's own marker for a SIGINT we sent: a quiet line, not a "User message" fold and
        // not an error (the matching `result` diagnostic is filtered in `applyResult`).
        v = appendBlock(v, null, {
          kind: 'notice',
          id: `interrupt:${typeof e.uuid === 'string' ? e.uuid : ts}`,
          level: 'info',
          variant: 'interrupted',
          title: 'Stopped by you',
          ts,
        });
      else
        v = appendBlock(v, null, {
          kind: 'notice',
          id,
          level: 'info',
          title: e.isSynthetic ? 'Injected context' : 'User message',
          text: b.text,
          ts,
        });
    } else if (parsed?.kind === 'malformed') {
      v = appendMalformed(v, parent, id, raw, parsed, ts);
    } else {
      v = { ...v, ignoredCount: v.ignoredCount + 1 };
    }
  });
  return v;
}

function mergeAgentResult(task: TaskInfo | undefined, structured: unknown): TaskInfo | undefined {
  const s = rec(structured);
  if (!s) return task;
  const status = taskStatus(s.status);
  const launched = isAsyncLaunch(structured);
  return {
    ...task,
    // `async_launched` is not a task status; the task is running until its own events say otherwise.
    status: status ?? task?.status ?? (launched ? 'running' : undefined),
    totalTokens: num(s.totalTokens) ?? task?.totalTokens,
    toolUses: num(s.totalToolUseCount) ?? task?.toolUses,
    durationMs: num(s.totalDurationMs) ?? task?.durationMs,
    subagentType: task?.subagentType ?? str(s.agentType),
    description: task?.description ?? str(s.description),
    async: launched ? true : task?.async,
  };
}

// ---------- result ----------

function applyResult(view: RunView, e: RawEvent): RunView {
  const subtype = str(e.subtype) ?? 'unknown';
  const isError = e.is_error === true || subtype !== 'success';
  // Only what the event actually reported. An absent figure stays absent all the way to the
  // display, because "not reported" and "zero" are different facts about a run.
  const numbers: RunNumbers = {
    costUsd: num(e.total_cost_usd),
    durationMs: num(e.duration_ms),
    durationApiMs: num(e.duration_api_ms),
    numTurns: num(e.num_turns),
  };
  const usage = buildUsage(e);
  // `[ede_diagnostic] …` is the CLI's internal note about *how* a turn ended (it accompanies every
  // interrupt), never something a user can act on; showing it turned a plain Stop into a red crash
  // box. The raw event keeps it for the inspector and the export.
  const errors = (
    Array.isArray(e.errors) ? e.errors.filter((x): x is string => typeof x === 'string') : []
  ).filter((x) => !x.startsWith('[ede_diagnostic]'));
  // A run we interrupted reports `error_during_execution` with nothing but that diagnostic. The
  // interrupt marker immediately precedes the result, so a run that ends right after it has no
  // error to report at all — the lifecycle turns it into `cancelled` ("Stopped by you").
  const interrupted = lastBlockIsInterrupt(view);
  const message = errors.length
    ? errors.join('; ')
    : isError && !interrupted
      ? `Run ended with ${subtype}`
      : undefined;
  // Deliberately no `status` and no `endedAt`: the server owns both (`applyLifecycle`). Guessing
  // `failed` from `is_error` here turned every Stop into a "Lane failed" toast and, after a reload,
  // into a red "Failed" footer on a run the server had recorded as `cancelled` (QA 2026-09-19). What
  // the event claimed is kept as `outcome`, which is what the server's import path derives status from.
  return {
    ...view,
    outcome: { subtype, isError },
    sessionId: str(e.session_id) ?? view.sessionId,
    numbers,
    usage,
    thinking: undefined,
    // A later result supersedes the prior result, including its diagnostic.
    error: message ? { message } : undefined,
  };
}

function lastBlockIsInterrupt(view: RunView): boolean {
  const last = view.blocks.at(-1);
  return last?.kind === 'notice' && last.variant === 'interrupted';
}

function buildUsage(e: RawEvent): RunUsage | undefined {
  const u = rec(e.usage);
  const perModelRaw = rec(e.modelUsage) ?? {};
  const perModel: Record<string, ModelUsageView> = {};
  for (const [model, raw] of Object.entries(perModelRaw)) {
    const m = rec(raw);
    if (!m) continue;
    perModel[model] = {
      inputTokens: num(m.inputTokens) ?? 0,
      outputTokens: num(m.outputTokens) ?? 0,
      cacheReadTokens: num(m.cacheReadInputTokens) ?? 0,
      cacheCreationTokens: num(m.cacheCreationInputTokens) ?? 0,
      thinkingTokens: num(m.thinkingTokens),
      costUsd: num(m.costUSD) ?? 0,
      contextWindow: num(m.contextWindow),
    };
  }
  if (!u && Object.keys(perModel).length === 0) return undefined;
  const details = rec(u?.output_tokens_details);
  return {
    inputTokens: num(u?.input_tokens) ?? 0,
    outputTokens: num(u?.output_tokens) ?? 0,
    cacheReadTokens: num(u?.cache_read_input_tokens) ?? 0,
    cacheCreationTokens: num(u?.cache_creation_input_tokens) ?? 0,
    thinkingTokens: num(details?.thinking_tokens),
    perModel,
  };
}

// ---------- system ----------

function applySystem(view: RunView, e: RawEvent, ts: number): RunView {
  switch (e.subtype) {
    case 'init': {
      const tools = Array.isArray(e.tools) ? e.tools.filter((t): t is string => typeof t === 'string') : [];
      return {
        ...view,
        model: str(e.model) ?? view.model,
        cwd: str(e.cwd) ?? view.cwd,
        sessionId: str(e.session_id) ?? view.sessionId,
        setup: {
          ...view.setup,
          claudeCodeVersion: str(e.claude_code_version),
          permissionMode: str(e.permissionMode),
          toolCount: Array.isArray(e.tools) ? e.tools.length : undefined,
          mcpServers: mcpServersOf(e),
          // What the CLI says it loaded, not what we asked for. A run started with `--chrome`
          // against a browser that is not there reports no browser tools at all, and that absence
          // is exactly what the console has to be able to show (see `BrowserStatus`).
          browserTools: tools.filter((t) => t.startsWith(CHROME_TOOL_PREFIX)),
        },
      };
    }
    case 'hook_started': {
      const hook: HookInfo = {
        id: str(e.hook_id) ?? `${ts}`,
        name: str(e.hook_name) ?? 'hook',
        event: str(e.hook_event) ?? '',
      };
      return { ...view, setup: { ...view.setup, hooks: [...view.setup.hooks, hook] } };
    }
    case 'hook_response': {
      const id = str(e.hook_id);
      const hooks = view.setup.hooks.map((h) =>
        h.id === id ? { ...h, outcome: str(e.outcome), exitCode: num(e.exit_code) } : h,
      );
      if (!hooks.some((h) => h.id === id))
        hooks.push({
          id: id ?? `${ts}`,
          name: str(e.hook_name) ?? 'hook',
          event: str(e.hook_event) ?? '',
          outcome: str(e.outcome),
          exitCode: num(e.exit_code),
        });
      return { ...view, setup: { ...view.setup, hooks } };
    }
    case 'thinking_tokens':
      return {
        ...view,
        thinking: { estimatedTokens: num(e.estimated_tokens) ?? 0, since: view.thinking?.since ?? ts },
      };
    case 'task_summary': {
      const detail = str(e.detail);
      return detail && detail !== 'None' ? { ...view, activity: detail } : view;
    }
    case 'post_turn_summary':
      return {
        ...view,
        summary: { category: str(e.status_category) ?? '', detail: str(e.status_detail) ?? '' },
      };
    case 'task_started':
    case 'task_progress':
    case 'task_notification':
      return applyTaskEvent(view, e, ts);
    case 'task_updated': {
      const taskId = str(e.task_id);
      const call = taskId ? findCallByTaskId(view, taskId) : undefined;
      const patch = rec(e.patch);
      if (!call || !patch) return view;
      const status = taskStatus(patch.status);
      return updateCall(view, call.id, (c) => {
        const task: TaskInfo = {
          ...c.task,
          status: status ?? c.task?.status,
          description: str(patch.description) ?? c.task?.description,
        };
        return status && TASK_TERMINAL.has(status) ? settleDelegateCall(c, task, status, ts) : { ...c, task };
      });
    }
    case 'api_retry': {
      const attempt = num(e.attempt);
      const max = num(e.max_retries);
      const delay = num(e.retry_delay_ms);
      const title = `API retry${attempt ? ` ${attempt}${max ? `/${max}` : ''}` : ''}`;
      const text = [str(e.error), delay ? `retrying in ${Math.round(delay / 1000)} s` : undefined]
        .filter(Boolean)
        .join(' · ');
      return {
        ...appendBlock(view, null, {
          kind: 'notice',
          id: `retry:${str(e.uuid) ?? ts}`,
          level: 'warning',
          title,
          text,
          ts,
        }),
        retries: view.retries + 1,
      };
    }
    case 'permission_denied': {
      const tool = str(e.tool_name) ?? 'tool';
      return {
        ...appendBlock(view, null, {
          kind: 'notice',
          id: `denied:${str(e.uuid) ?? ts}`,
          level: 'error',
          title: `Permission denied: ${tool}`,
          text: str(e.message),
          ts,
        }),
        deniedCount: view.deniedCount + 1,
      };
    }
    default:
      return { ...view, ignoredCount: view.ignoredCount + 1 };
  }
}

function applyTaskEvent(view: RunView, e: RawEvent, ts: number): RunView {
  const toolUseId = str(e.tool_use_id);
  const taskId = str(e.task_id);
  const call =
    (toolUseId ? getCall(view, toolUseId) : undefined) ??
    (taskId ? findCallByTaskId(view, taskId) : undefined);
  if (!call) return { ...view, ignoredCount: view.ignoredCount + 1 };
  const usage = rec(e.usage);
  const status: TaskInfo['status'] | undefined =
    e.subtype === 'task_started' ? 'running' : taskStatus(e.status);
  const started = e.subtype === 'task_started';
  return updateCall(view, call.id, (c) => {
    const task: TaskInfo = {
      ...c.task,
      taskId: taskId ?? c.task?.taskId,
      // `task_started` brings the delegate's brief; `task_progress` reuses the same field for the
      // step it is on right now, which must not replace that brief — it becomes `activity`.
      description: started
        ? (str(e.description) ?? c.task?.description)
        : (c.task?.description ?? str(call.input.description)),
      activity: started ? c.task?.activity : (str(e.description) ?? c.task?.activity),
      subagentType: str(e.subagent_type) ?? c.task?.subagentType,
      spawnDepth: num(e.spawn_depth) ?? c.task?.spawnDepth,
      status: status ?? c.task?.status,
      totalTokens: num(usage?.total_tokens) ?? c.task?.totalTokens,
      toolUses: num(usage?.tool_uses) ?? c.task?.toolUses,
      durationMs: num(usage?.duration_ms) ?? c.task?.durationMs,
      lastToolName: str(e.last_tool_name) ?? c.task?.lastToolName,
      summary: str(e.summary) ?? c.task?.summary,
    };
    return status && TASK_TERMINAL.has(status) ? settleDelegateCall(c, task, status, ts) : { ...c, task };
  });
}

/**
 * The delegate is done: the Agent call finally gets an outcome, and its result becomes the task's
 * own report (`task_notification.summary`) instead of the launch bookkeeping. Duration comes from
 * the task's usage when it reported any, otherwise from the call's own span — the CLI's `result`
 * duration excludes subagent wall time.
 */
function settleDelegateCall(
  c: ToolCall,
  task: TaskInfo,
  status: NonNullable<TaskInfo['status']>,
  ts: number,
): ToolCall {
  const ok = status === 'completed';
  return {
    ...c,
    status: ok ? 'done' : 'error',
    task: { ...task, completedAt: task.completedAt ?? ts },
    result: {
      text: task.summary ?? c.result?.text ?? '',
      isError: !ok,
      ts,
      structured: c.result?.structured,
      internal: false,
    },
    durationMs: task.durationMs ?? Math.max(0, ts - c.ts),
  };
}

// ---------- misc ----------

function applyRateLimit(view: RunView, e: RawEvent): RunView {
  const info = rec(e.rate_limit_info);
  if (!info) return view;
  return {
    ...view,
    rateLimit: {
      status: str(info.status) ?? 'unknown',
      type: str(info.rateLimitType),
      utilization: num(info.utilization),
      resetsAt: num(info.resetsAt),
    },
  };
}

function applyToolProgress(view: RunView, e: RawEvent): RunView {
  const id = str(e.tool_use_id);
  if (!id || !getCall(view, id)) return view;
  return updateCall(view, id, (c) => ({
    ...c,
    elapsedSeconds: num(e.elapsed_time_seconds) ?? c.elapsedSeconds,
  }));
}

export function applyLifecycle(view: RunView, lc: RunLifecycle): RunView {
  const movingBackwards = TERMINAL_STATUSES.has(view.status) && lc.status === 'running';
  const status = movingBackwards ? view.status : lc.status;
  return {
    ...view,
    status,
    origin: lc.origin,
    startedAt: lc.startedAt || view.startedAt,
    endedAt: lc.endedAt ?? view.endedAt,
    sessionId: lc.sessionId ?? view.sessionId,
    numbers: lc.numbers ?? view.numbers,
    // A stop the user asked for is not a failure: whatever the interrupted turn's `result` claimed
    // (an `error_during_execution` with a diagnostic, see `applyResult`) is dropped, and the run
    // reads "Stopped by you" instead of a red error box.
    error: status === 'cancelled' ? undefined : (lc.error ?? view.error),
    resumable: lc.resumable ?? view.resumable,
    thinking: TERMINAL_STATUSES.has(lc.status) ? undefined : view.thinking,
  };
}
