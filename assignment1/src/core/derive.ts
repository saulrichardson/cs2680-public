import { TOOL_CLASSES } from './classify';
import { countPatchLines } from './patches';
import { getCall } from './reducer';
import { TERMINAL_STATUSES } from './status';
import { summarizeInput } from './summarize';
import type {
  Block,
  MetricGaps,
  OutlineItem,
  RunNumbers,
  RunSummary,
  RunView,
  TimelineBar,
  ToolCall,
  ToolClass,
} from './types';

/**
 * Every tool call in the run, depth-first, with its depth and parent.
 *
 * `seen` is a second line of defence, not the rule: the reducer refuses to build a cyclic ancestry
 * in the first place (`wouldCycle`), so a cycle reaching here means a view was assembled some other
 * way. Walking it anyway would recurse until the stack gave out, which is a poor way to learn that
 * an imported recording was malformed.
 */
export function* walkCalls(
  view: RunView,
  blocks: Block[],
  depth: number,
  parent: ToolCall | null,
  seen: Set<string> = new Set(),
): Generator<{ call: ToolCall; depth: number; parent: ToolCall | null }> {
  for (const b of blocks) {
    if (b.kind !== 'tool') continue;
    const call = getCall(view, b.callId);
    if (!call || seen.has(call.id)) continue;
    seen.add(call.id);
    yield { call, depth, parent };
    if (call.children.length) yield* walkCalls(view, call.children, depth + 1, call, seen);
  }
}

export function deriveOutline(view: RunView, cwd?: string): OutlineItem[] {
  const items: OutlineItem[] = [];
  for (const { call, depth } of walkCalls(view, view.blocks, 0, null)) {
    items.push({
      callId: call.id,
      name: call.name,
      toolClass: call.toolClass,
      status: call.status,
      depth,
      summary: summarizeInput(call.name, call.input, cwd ?? view.cwd).primary,
    });
  }
  return items;
}

export function deriveTimeline(view: RunView, now: number): TimelineBar[] {
  const bars: TimelineBar[] = [];
  // How far an unanswered call may run. While the run is going that is the wall clock; once the run
  // has reached a terminal state it is the run's own `endedAt`, because nothing can have happened
  // in it since. Without this, a cancelled run's pending call kept accruing time for as long as the
  // page was open and its bar left the axis entirely (readiness review R8).
  const openUntil = TERMINAL_STATUSES.has(view.status) && view.endedAt !== undefined ? view.endedAt : now;
  for (const { call, depth, parent } of walkCalls(view, view.blocks, 0, null)) {
    // A still-pending call runs to `openUntil` even when it already carries a result: an async
    // delegate gets its launch receipt in milliseconds and keeps working for minutes (see
    // `isAsyncLaunch`). It keeps `status: 'pending'`, so a call that never produced a result is
    // never drawn as one that succeeded — it is drawn as one that was still open when time ran out.
    const end = call.status === 'pending' ? Math.max(openUntil, call.ts) : (call.result?.ts ?? call.ts);
    bars.push({
      callId: call.id,
      name: call.name,
      toolClass: call.toolClass,
      status: call.status,
      depth,
      start: call.ts,
      end: Math.max(end, call.ts),
      parentCallId: parent?.id ?? null,
    });
  }
  return bars;
}

/**
 * The time span one run's bars are drawn on.
 *
 * The run's own clock decides the start and (once it is over) the end, but the axis is always
 * widened to contain the bars: a replay re-bases its event stamps on the new run's start while
 * copying the recording's duration into `endedAt`, so a recorded result can legitimately sit after
 * the declared end. A live run runs to the wall clock. The span is never empty, so callers can
 * divide by it.
 */
export function timelineSpan(
  view: RunView,
  bars: TimelineBar[],
  now: number,
): { start: number; end: number } {
  const start = view.startedAt;
  const open = TERMINAL_STATUSES.has(view.status) && view.endedAt !== undefined ? view.endedAt : now;
  let end = Math.max(open, start + 1);
  for (const b of bars) end = Math.max(end, b.end);
  return { start, end };
}

export function summarizeRun(view: RunView): RunSummary {
  const callsByClass = Object.fromEntries(TOOL_CLASSES.map((c) => [c, 0])) as Record<ToolClass, number>;
  const files = new Set<string>();
  let errorCount = 0;
  let subagentCount = 0;
  let added = 0;
  let removed = 0;
  const calls = Object.values(view.callsById);
  for (const c of calls) {
    callsByClass[c.toolClass] += 1;
    if (c.status === 'error') errorCount += 1;
    if (c.toolClass === 'delegate') subagentCount += 1;
    for (const p of c.patches) files.add(p.filePath);
    const counts = countPatchLines(c.patches);
    added += counts.added;
    removed += counts.removed;
  }
  const textBlocks = countText(view.blocks, view);
  return {
    callCount: calls.length,
    callsByClass,
    errorCount,
    subagentCount,
    filesTouched: [...files].sort(),
    linesAdded: added,
    linesRemoved: removed,
    textBlocks,
  };
}

/**
 * The numbers for a candidate produced by several executions — a first attempt and the follow-ups
 * that continued it. They are sums, because that is what the lane actually spent to reach the state
 * its worktree is in now: showing only the latest prompt's cost next to a diff spanning all of them
 * is the accounting mismatch the readiness review called out (R3). Runs that reported nothing
 * contribute nothing. Everything is labelled as a total wherever it is displayed.
 */
export function aggregateNumbers(parts: (RunNumbers | undefined)[]): {
  numbers: RunNumbers | undefined;
  gaps: MetricGaps;
} {
  const sum = (pick: (n: RunNumbers) => number | undefined) => {
    let total: number | undefined;
    let missing = 0;
    for (const part of parts) {
      const value = part === undefined ? undefined : pick(part);
      // A figure nobody reported stays undefined; one reported as 0 is a real zero and adds 0.
      if (value === undefined) missing += 1;
      else total = (total ?? 0) + value;
    }
    return { total, missing };
  };
  const cost = sum((n) => n.costUsd);
  const duration = sum((n) => n.durationMs);
  const api = sum((n) => n.durationApiMs);
  const turns = sum((n) => n.numTurns);
  const numbers =
    cost.total === undefined &&
    duration.total === undefined &&
    api.total === undefined &&
    turns.total === undefined
      ? undefined
      : {
          costUsd: cost.total,
          durationMs: duration.total,
          durationApiMs: api.total,
          numTurns: turns.total,
        };
  return {
    numbers,
    gaps: { costUsd: cost.missing, durationMs: duration.missing, numTurns: turns.missing },
  };
}

/** As `aggregateNumbers`, for what the attempts *did*: counts add up, touched files are a union. */
export function aggregateSummaries(parts: RunSummary[]): RunSummary | undefined {
  if (parts.length === 0) return undefined;
  const callsByClass = Object.fromEntries(TOOL_CLASSES.map((c) => [c, 0])) as Record<ToolClass, number>;
  const files = new Set<string>();
  const total: RunSummary = {
    callCount: 0,
    callsByClass,
    errorCount: 0,
    subagentCount: 0,
    filesTouched: [],
    linesAdded: 0,
    linesRemoved: 0,
    textBlocks: 0,
  };
  for (const part of parts) {
    total.callCount += part.callCount;
    total.errorCount += part.errorCount;
    total.subagentCount += part.subagentCount;
    total.linesAdded += part.linesAdded;
    total.linesRemoved += part.linesRemoved;
    total.textBlocks += part.textBlocks;
    for (const c of TOOL_CLASSES) callsByClass[c] += part.callsByClass[c];
    for (const f of part.filesTouched) files.add(f);
  }
  total.filesTouched = [...files].sort();
  return total;
}

function countText(blocks: Block[], view: RunView, seen: Set<string> = new Set()): number {
  let n = 0;
  for (const b of blocks) {
    if (b.kind === 'text') n += 1;
    if (b.kind === 'tool') {
      const call = getCall(view, b.callId);
      if (call && !seen.has(call.id)) {
        seen.add(call.id);
        n += countText(call.children, view, seen);
      }
    }
  }
  return n;
}
