import type { RunStatus } from './types';

/**
 * The statuses a run never leaves.
 *
 * One definition, because five places disagreeing about it would each fail differently: the
 * reducer's "a lifecycle never moves a finished run back to running" guard, the replayer's "only a
 * settled run may be replayed, and only a settled status may be copied onto the replay",
 * `lanes.close`'s wait for a cancel to land, and the two UI components that stop drawing a pending
 * call as live once its run is over.
 */
export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  'finished',
  'failed',
  'cancelled',
]);

/**
 * The same question for callers that hold a plain string rather than a `RunStatus` — the `runs`
 * table stores `status` as untyped text, so a row read straight from the database arrives as one.
 * `undefined` (no such run) is not terminal.
 */
export function isTerminal(status: string | undefined): boolean {
  const known: ReadonlySet<string> = TERMINAL_STATUSES;
  return status !== undefined && known.has(status);
}
