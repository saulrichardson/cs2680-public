'use client';
import { useEffect, useRef } from 'react';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';
import { MissionSocket, socketUrl } from './client';

/** How long a gap in a run's sequence may sit unfilled before we ask the hub to resend it. */
const GAP_TOLERANCE_MS = 1500;
/** Never ask more often than this for the same run, however long the gap persists. */
const RESEND_COOLDOWN_MS = 5000;

/** Owns the single WebSocket; subscribes to every open lane and resumes by last seq after reconnects. */
export function SocketBridge() {
  const socketRef = useRef<MissionSocket | null>(null);
  const laneOrder = useMissionStore((s) => s.laneOrder);
  const subscribedRef = useRef<Set<string>>(new Set());
  const lastResendRef = useRef<Map<string, number>>(new Map());
  const utils = trpc.useUtils();

  useEffect(() => {
    const store = useMissionStore.getState();
    const socket = new MissionSocket(socketUrl(), {
      onOpen: () => {
        store.setConnection('open');
        subscribedRef.current = new Set();
        const lanes = useMissionStore.getState().laneOrder;
        if (lanes.length) {
          // The subscription carries this client's event cursors; the server answers with each
          // lane's current runs and their lifecycle, which is how anything that happened while the
          // socket was down — a run ending, a run failing with no events at all — is recovered.
          socket.send({
            kind: 'subscribe',
            laneIds: lanes,
            resume: useMissionStore.getState().lastSeqByRun(),
          });
          subscribedRef.current = new Set(lanes);
        }
        // A run whose events could not be fetched is worth another try now: the interruption that
        // made that fetch fail is usually the one that closed this socket.
        store.retryFailedEvents();
        // Two things a subscription cannot carry, refreshed together on every (re)connect:
        //  - lanes, which are outside any subscription: one created or closed while this client was
        //    away is not in `laneOrder` to be asked about. Refetching brings it in, and the effect
        //    below then subscribes to it.
        //  - each run's prompt and directory, which the snapshot deliberately leaves out. One
        //    request per lane covers every run in it, including any that appeared while away.
        // Neither is *relied* on: a run the snapshot names but this client cannot describe is
        // recorded in `unknownRuns` and fetched on its own, so no ordering between these responses
        // and the snapshot can leave a run permanently without its metadata.
        void utils.lanes.list.invalidate();
        void utils.lanes.get.invalidate();
        void utils.projects.list.invalidate();
        void utils.wrapups.sources.invalidate();
        void utils.runs.list.invalidate();
      },
      onClose: () => store.setConnection('closed'),
      onMessage: (msg) => {
        if (msg.kind === 'events') useMissionStore.getState().ingest(msg.envelopes);
        else if (msg.kind === 'run') useMissionStore.getState().applyLifecycle(msg.lifecycle);
        else if (msg.kind === 'lane-state') useMissionStore.getState().reconcileLane(msg.laneId, msg.runs);
        else if (msg.kind === 'lanes-changed') {
          void utils.lanes.list.invalidate();
          void utils.lanes.get.invalidate();
          void utils.projects.list.invalidate();
        }
      },
    });
    socketRef.current = socket;
    socket.connect();
    return () => socket.close();
  }, [utils]);

  // Lanes come and go while the socket stays: subscribe to the new ones, and stop the hub sending
  // events for lanes that have closed (the store has already dropped their runs).
  useEffect(() => {
    const socket = socketRef.current;
    if (!socket) return;
    const open = new Set(laneOrder);
    const fresh = laneOrder.filter((id) => !subscribedRef.current.has(id));
    if (
      fresh.length &&
      socket.send({ kind: 'subscribe', laneIds: fresh, resume: useMissionStore.getState().lastSeqByRun() })
    ) {
      for (const id of fresh) subscribedRef.current.add(id);
    }
    const stale = [...subscribedRef.current].filter((id) => !open.has(id));
    if (stale.length && socket.send({ kind: 'unsubscribe', laneIds: stale })) {
      for (const id of stale) subscribedRef.current.delete(id);
    }
  }, [laneOrder]);

  /**
   * Gap recovery. `ingest` buffers any envelope that arrives ahead of a run's sequence, waiting for
   * the missing one. If that one was simply lost (a frame dropped on a flaky connection), nothing
   * would ever fill the gap and the run would stop updating in a way no reconnect notices — the
   * socket is still open. So a gap older than `GAP_TOLERANCE_MS` re-subscribes the run's lane with
   * `resume: { [runId]: lastSeq }`: the hub drops what it has buffered for that run and backfills
   * from the database, which is the authoritative copy. At most one such request per run per
   * `RESEND_COOLDOWN_MS`, so a run whose gap cannot be filled costs one small request every 5 s.
   */
  useEffect(() => {
    const timer = setInterval(() => {
      const socket = socketRef.current;
      if (!socket) return;
      const { pendingSince, runs } = useMissionStore.getState();
      const now = Date.now();
      for (const [runId, since] of Object.entries(pendingSince)) {
        if (now - since < GAP_TOLERANCE_MS) continue;
        if (now - (lastResendRef.current.get(runId) ?? 0) < RESEND_COOLDOWN_MS) continue;
        const run = runs[runId];
        if (!run) {
          lastResendRef.current.delete(runId);
          continue;
        }
        if (socket.send({ kind: 'subscribe', laneIds: [run.laneId], resume: { [runId]: run.lastSeq } }))
          lastResendRef.current.set(runId, now);
      }
    }, GAP_TOLERANCE_MS);
    return () => clearInterval(timer);
  }, []);

  return null;
}
