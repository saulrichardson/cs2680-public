'use client';
import { useEffect, useRef } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';

/** Backoff schedule for retrying a failed `runs.events` fetch: 1s, 3s, 9s, then give up. */
const RETRY_DELAYS_MS = [1000, 3000, 9000];

/** Loads the lane's runs, then each run's events once (after the last seq already in the store). */
export function useLaneHydration(laneId: string) {
  const utils = trpc.useUtils();
  const runsQuery = trpc.runs.list.useQuery({ laneId });
  const runIds = useMissionStore(useShallow((s) => s.runsByLane[laneId] ?? []));
  // Only re-run the hydration effect when this lane's own run ids or their loaded flags change,
  // not on every store update that touches the (lane-wide) eventsLoaded map.
  const loadedFlags = useMissionStore(useShallow((s) => runIds.map((id) => s.eventsLoaded[id] ?? false)));
  const attemptsRef = useRef(new Map<string, number>());
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    if (runsQuery.data) useMissionStore.getState().hydrateRuns(runsQuery.data);
  }, [runsQuery.data]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: loadedFlags isn't read in the body (the loop re-reads eventsLoaded fresh from the store) but must stay a dependency so a runId flipping to loaded elsewhere still re-triggers this effect.
  useEffect(() => {
    const fetchEvents = (runId: string) => {
      const store = useMissionStore.getState();
      const afterSeq = store.runs[runId]?.lastSeq ?? 0;
      utils.client.runs.events
        .query({ runId, afterSeq })
        .then((envs) => {
          attemptsRef.current.delete(runId);
          timersRef.current.delete(runId);
          useMissionStore.getState().ingest(envs);
        })
        .catch((err) => {
          const attempt = attemptsRef.current.get(runId) ?? 0;
          if (attempt < RETRY_DELAYS_MS.length) {
            attemptsRef.current.set(runId, attempt + 1);
            const timer = setTimeout(() => {
              timersRef.current.delete(runId);
              fetchEvents(runId);
            }, RETRY_DELAYS_MS[attempt]);
            timersRef.current.set(runId, timer);
          } else {
            attemptsRef.current.delete(runId);
            timersRef.current.delete(runId);
            // The run stays marked loaded (below) so the effect never retries it on its own —
            // no request storm against a persistently failing server. But the lane must say so:
            // without the flag the run renders as one that streamed no events at all, which is
            // indistinguishable from an agent that did nothing.
            useMissionStore.getState().markEventsFailed(runId);
            console.warn('events hydration gave up for run', runId, err);
          }
        });
    };

    const store = useMissionStore.getState();
    for (const runId of runIds) {
      if (store.eventsLoaded[runId]) continue;
      store.markEventsLoaded(runId);
      fetchEvents(runId);
    }
  }, [runIds, loadedFlags, utils]);

  // Pending backoff timers are held in refs so they outlive individual effect re-runs (triggered by
  // e.g. a new run appearing) and are only ever cleared here, on unmount.
  useEffect(
    () => () => {
      for (const timer of timersRef.current.values()) clearTimeout(timer);
      timersRef.current.clear();
      attemptsRef.current.clear();
    },
    [],
  );

  return runsQuery;
}
