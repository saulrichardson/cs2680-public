/**
 * Which call, in which run.
 *
 * A tool call id is the CLI's, and the CLI only promises it is unique within its own session. Replay
 * a recording into the lane it came from — or import the same `events.jsonl` twice — and the lane
 * holds several calls with the same id. Everything that points at a call therefore points with both
 * halves: selection, the active outline row, jump requests, unfold requests, the DOM ids the jump
 * scrolls to, and the nodes of the session's agent graph. Pointing with the call id alone sent the
 * inspector to whichever copy came first (readiness review R7).
 *
 * This lives in `core` rather than beside the store because two layers now depend on the pair being
 * *the same* pair: `core/agents.ts` names a delegate node by it, and the browser selects, jumps to
 * and scrolls to a call by it. Two copies of the separator convention would drift.
 */
export interface CallRef {
  runId: string;
  callId: string;
}

/** One string for the pair, for map keys and DOM ids. `~` appears in neither id. */
export function callKey(ref: CallRef): string {
  return `${ref.runId}~${ref.callId}`;
}

export function sameCall(a: CallRef | null | undefined, b: CallRef | null | undefined): boolean {
  return !!a && !!b && a.runId === b.runId && a.callId === b.callId;
}
