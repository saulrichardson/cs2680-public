import { TERMINAL_STATUSES } from '@/core/status';
import type { CompareLane, RunView } from '@/core/types';
import { isPlayback } from '@/core/types';

/** What the client knows about the runs in a race's lanes, straight from the store. */
export interface KnownRuns {
  runsByLane: Record<string, string[]>;
  runs: Record<string, RunView>;
}

/** The executions this client knows of in a race's lanes, in the order the lane holds them. */
function executionsIn(lanes: CompareLane[], known: KnownRuns): RunView[] {
  return lanes.flatMap((lane) =>
    (known.runsByLane[lane.laneId] ?? [])
      .map((runId) => known.runs[runId])
      // A replay or an import in a racing lane changed no file in its worktree, so it is not a
      // candidate and must not make Compare churn (readiness review R3).
      .filter((run): run is RunView => run !== undefined && !isPlayback(run.origin)),
  );
}

/**
 * Has this comparison been overtaken by what the client already knows?
 *
 * Compare polls a server snapshot, and that snapshot decided when to stop polling — by looking at
 * the runs *it* contained. A follow-up execution started from another tab is in none of them, so an
 * open Compare stayed on the old answer until someone reloaded the page.
 *
 * The question is asked the other way round here: for the *lanes* of this race, does the client know
 * of an execution the snapshot does not, or of one that has since reached a terminal state the
 * snapshot has not caught up with?
 *
 * Deliberately one-directional. A snapshot that knows more than this client — the usual case for a
 * lane whose events have not been hydrated yet — is not stale, so a client that is merely behind
 * cannot drive a refetch loop.
 */
export function compareIsStale(lanes: CompareLane[], known: KnownRuns): boolean {
  for (const lane of lanes) {
    const attempts = new Map(lane.attempts.map((a) => [a.runId, a.status]));
    for (const run of executionsIn([lane], known)) {
      const status = attempts.get(run.runId);
      // an execution this race's snapshot has never heard of
      if (status === undefined) return true;
      // or one the client has watched finish since the snapshot was taken
      if (status !== run.status && TERMINAL_STATUSES.has(run.status)) return true;
    }
  }
  return false;
}

/** Is any execution in this race's lanes running, as far as this client knows? */
export function raceIsRunning(lanes: CompareLane[], known: KnownRuns): boolean {
  return executionsIn(lanes, known).some((run) => run.status === 'running');
}

/**
 * How often an open Compare should ask the server again — `false` when it has no reason to.
 *
 * Three reasons, and the third is the one that took two attempts to get right:
 *
 * 1. the snapshot itself says a lane is running;
 * 2. this client knows of an execution that is running, which the snapshot may predate;
 * 3. **the snapshot is behind what this client knows, for any reason at all.**
 *
 * Refetching on the *edge* of (3) — when staleness flips false→true — is not enough. A comparison
 * reads each lane's executions before it awaits git, so a follow-up that starts and finishes during
 * that wait produces a response that was already out of date when it was sent. The page then holds
 * a payload that is behind the store, with staleness true both before and after, nothing running
 * anywhere, and no further socket event coming: an edge-triggered refetch never fires again and the
 * page stays wrong until it is reloaded.
 *
 * So staleness *is* a reason to keep asking, at the same pace as a live race, until the answer
 * catches up. It is self-limiting: the condition is false as soon as a response arrives that
 * accounts for what this client knows, and one poll per interval is the same bounded traffic a
 * running race already costs (the server coalesces concurrent requests for a group).
 */
export function comparePollMs(
  lanes: CompareLane[] | undefined,
  known: KnownRuns,
  pollMs: number,
): number | false {
  if (!lanes) return false;
  if (lanes.some((lane) => lane.status === 'running')) return pollMs;
  if (raceIsRunning(lanes, known)) return pollMs;
  return compareIsStale(lanes, known) ? pollMs : false;
}
