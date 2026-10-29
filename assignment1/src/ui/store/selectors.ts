'use client';
import { useMemo } from 'react';
import type { LaneDto, RunStatus, RunView } from '@/core/types';
import { useMissionStore } from './missionStore';

/**
 * How a session is described in a list, in one place.
 *
 * Home, the project rail and the session header all answer "what is this session doing?" and were
 * each answering it slightly differently — one took the newest run by array position, another by
 * `startedAt`, a third counted anything `running` anywhere. Array position is the one that was
 * actually wrong: runs arrive over a socket and a reconnect backfills them, so "the last one added
 * to the map" is not "the latest run" and a finished session could keep reporting a stale status.
 */
export type SessionStatus = RunStatus | 'idle';

/** The run that describes this session now: the one that started most recently. */
export function latestRun(runs: Record<string, RunView>, laneId: string): RunView | undefined {
  let latest: RunView | undefined;
  for (const run of Object.values(runs)) {
    if (run.laneId !== laneId) continue;
    // `startedAt` then id, so two runs stamped in the same millisecond still order deterministically
    // instead of by whichever the socket happened to deliver first.
    if (
      !latest ||
      run.startedAt > latest.startedAt ||
      (run.startedAt === latest.startedAt && run.runId > latest.runId)
    )
      latest = run;
  }
  return latest;
}

export function sessionStatus(runs: Record<string, RunView>, laneId: string): SessionStatus {
  // Running wins wherever it is: a session with an agent working in it is running, even if some
  // later row was written first.
  for (const run of Object.values(runs))
    if (run.laneId === laneId && run.status === 'running') return 'running';
  return latestRun(runs, laneId)?.status ?? 'idle';
}

export function useLaneStatus(laneId: string): SessionStatus {
  return useMissionStore((s) => sessionStatus(s.runs, laneId));
}

/**
 * The run Stop should reach: the one still going, not the newest row.
 *
 * They differ exactly when it matters — an import or a replay dropped into a session mid-run is a
 * newer row than the agent that is still working.
 */
export function useLaneActive(laneId: string): RunView | undefined {
  return useMissionStore((s) => {
    let active: RunView | undefined;
    for (const run of Object.values(s.runs))
      if (run.laneId === laneId && run.status === 'running' && (!active || run.startedAt > active.startedAt))
        active = run;
    return active;
  });
}

/**
 * The order a project's sessions are listed in: running first, then by last activity.
 *
 * Recency is activity, not creation. A session answered a minute ago belongs above one created
 * yesterday and never used, and dropping a session merely because its run finished is what made
 * finished work look as though it had disappeared.
 */
export function orderSessions(lanes: LaneDto[], runs: Record<string, RunView>): LaneDto[] {
  return [...lanes].sort((a, b) => {
    const ra = sessionStatus(runs, a.id) === 'running' ? 0 : 1;
    const rb = sessionStatus(runs, b.id) === 'running' ? 0 : 1;
    return ra - rb || b.lastActivityAt - a.lastActivityAt;
  });
}

export function useOrderedSessions(lanes: LaneDto[]): LaneDto[] {
  const runs = useMissionStore((s) => s.runs);
  return useMemo(() => orderSessions(lanes, runs), [lanes, runs]);
}

/** How many sessions have an agent working in them right now, across everything loaded. */
export function useRunningCount(): number {
  return useMissionStore((s) => {
    const lanes = new Set<string>();
    for (const run of Object.values(s.runs)) if (run.status === 'running') lanes.add(run.laneId);
    // Only sessions this client still knows about: a session archived elsewhere is removed from
    // `lanes`, and counting its leftover runs would keep an indicator lit for work that is gone.
    let n = 0;
    for (const id of lanes) if (s.lanes[id]) n += 1;
    return n;
  });
}
