import { getCall } from '@/core/reducer';
import { type CallRef, callKey } from '@/core/ref';
import type { Block, RunView, ToolCall } from '@/core/types';

/** Guards against a malformed `parent_tool_use_id` cycle; real trajectories nest a handful deep. */
const MAX_CALL_DEPTH = 32;

// The pair identity itself lives in `@/core/ref`, because the reducer-side agent graph names its
// nodes with the same pair. Re-exported here so every browser-side call site keeps one import.
export { type CallRef, callKey, sameCall } from '@/core/ref';

/** The DOM id of a call's card. */
export function callElementId(ref: CallRef): string {
  return `call-${callKey(ref)}`;
}

/**
 * The chain of tool call ids from the row that `flattenRows` emits down to `ref`, or `null` when
 * that run does not hold that call. A nested call is not a row of its own — it is rendered inside
 * its parent's `SubagentGroup` — so jumping to one means scrolling to `path[0]` and opening every
 * group on the way down. A top-level call returns just its own id.
 *
 * The walk stops at the first ancestor the run does not know, which is exactly where the reducer's
 * `appendBlock` gives up and puts the block on the main thread: an orphaned call *is* a row.
 */
export function callPath(runs: RunView[], ref: CallRef): string[] | null {
  const run = runs.find((r) => r.runId === ref.runId);
  const target = run ? getCall(run, ref.callId) : undefined;
  if (!run || !target) return null;
  const path = [target.id];
  let call: ToolCall = target;
  for (let i = 0; i < MAX_CALL_DEPTH; i += 1) {
    const parentId = call.parentToolUseId;
    if (!parentId) break;
    const parent = getCall(run, parentId);
    if (!parent || path.includes(parent.id)) break;
    path.unshift(parent.id);
    call = parent;
  }
  return path;
}

export type Row =
  | { key: string; kind: 'prompt'; runId: string }
  | { key: string; kind: 'block'; runId: string; block: Block }
  | { key: string; kind: 'footer'; runId: string };

export function flattenRows(runs: RunView[]): Row[] {
  const rows: Row[] = [];
  for (const run of runs) {
    rows.push({ key: `${run.runId}:prompt`, kind: 'prompt', runId: run.runId });
    for (const block of run.blocks)
      rows.push({ key: `${run.runId}:${blockKey(block)}`, kind: 'block', runId: run.runId, block });
    rows.push({ key: `${run.runId}:footer`, kind: 'footer', runId: run.runId });
  }
  return rows;
}

function blockKey(block: Block): string {
  return block.kind === 'tool' ? `tool:${block.callId}` : `${block.kind}:${block.id}`;
}
