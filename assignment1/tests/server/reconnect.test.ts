import { createServer, type Server } from 'node:http';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import WebSocket from 'ws';
import type { WsServerMessage } from '@/core/types';
import { type Db, openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { Hub } from '@/server/ws/hub';
import { useMissionStore } from '@/ui/store/missionStore';

/**
 * R1, end to end: the real hub, the real browser store, and a socket that really goes away.
 *
 * A subscription used to carry event cursors and nothing else, so everything that happened while a
 * client was disconnected and was *not* an event — a run reaching its terminal state, a run
 * starting and finishing, a run that failed before emitting anything at all — was simply missed.
 * The client kept showing a finished run as running, with its Stop button live, until someone
 * reloaded the page. A subscription now answers with the lane's current run inventory and
 * lifecycle, which the server is the authority on, alongside the event backfill.
 */

let server: Server;
let hub: Hub;
let db: Db;
let url: string;

const RESULT = {
  type: 'result',
  subtype: 'success',
  session_id: 'sess-1',
  duration_ms: 1000,
  total_cost_usd: 0.1,
  num_turns: 1,
};

function insertEvent(runId: string, seq: number, event: unknown, receivedAt = seq): void {
  repo.insertEvent(db, {
    runId,
    seq,
    receivedAt,
    type: (event as { type: string }).type,
    parentToolUseId: null,
    json: JSON.stringify(event),
  });
}

function makeRun(id: string, laneId: string, status = 'running', startedAt = 1000): void {
  repo.createRun(db, {
    id,
    laneId,
    prompt: id,
    effectiveCwd: '/tmp',
    permission: 'allowlist',
    origin: 'execution',
    status,
    startedAt,
  });
}

/** Connect, drive the store from every frame the way `SocketBridge` does, and resolve on an ack. */
function connect(): Promise<{ ws: WebSocket; subscribe: (resume: Record<string, number>) => Promise<void> }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString()) as WsServerMessage;
      const store = useMissionStore.getState();
      if (msg.kind === 'events') store.ingest(msg.envelopes);
      else if (msg.kind === 'run') store.applyLifecycle(msg.lifecycle);
      else if (msg.kind === 'lane-state') store.reconcileLane(msg.laneId, msg.runs);
    });
    ws.on('open', () =>
      resolve({
        ws,
        subscribe: (resume) =>
          new Promise<void>((done) => {
            const onAck = (data: WebSocket.RawData) => {
              if ((JSON.parse(data.toString()) as WsServerMessage).kind === 'subscribed') {
                ws.off('message', onAck);
                done();
              }
            };
            ws.on('message', onAck);
            ws.send(JSON.stringify({ kind: 'subscribe', laneIds: ['lane'], resume }));
          }),
      }),
    );
    ws.on('error', reject);
  });
}

const closed = (ws: WebSocket) => new Promise<void>((r) => ws.on('close', () => r()));

beforeAll(async () => {
  ({ db } = openDb(':memory:'));
  repo.createLane(db, { id: 'lane', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
  hub = new Hub({ db, flushMs: 5 });
  const srv = createServer((_req, res) => res.end('ok'));
  srv.on('upgrade', (req, socket, head) => {
    if (new URL(req.url ?? '/', 'http://localhost').pathname === '/ws') hub.handleUpgrade(req, socket, head);
    else socket.destroy();
  });
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', () => r()));
  const addr = srv.address();
  if (!addr || typeof addr === 'string') throw new Error('no address');
  server = srv;
  url = `ws://127.0.0.1:${addr.port}/ws`;
});

afterAll(async () => {
  await hub.close();
  await new Promise<void>((r) => server.close(() => r()));
});

beforeEach(() => useMissionStore.getState().reset());

describe('reconnecting to a lane', () => {
  it('recovers a run that finished while the client was away', async () => {
    makeRun('offline-end', 'lane');
    useMissionStore.getState().hydrateLanes([
      {
        id: 'lane',
        name: 'L',
        cwd: '/tmp',
        permission: 'allowlist',
        groupId: null,
        groupIndex: null,
        createdAt: 1,
        archivedAt: null,
        projectRoot: '/tmp',
        lastActivityAt: 1,
        isolated: false,
      },
    ]);
    useMissionStore.getState().hydrateRuns([
      {
        ...repo.lifecycleOf(repo.getRun(db, 'offline-end') as never),
        prompt: 'p',
        cwd: '/tmp',
        browser: 'chrome' as const,
        resumable: false,
      },
    ]);

    const first = await connect();
    await first.subscribe({ 'offline-end': 0 });
    expect(useMissionStore.getState().runs['offline-end']?.status).toBe('running');
    first.ws.close();
    await closed(first.ws);

    // while nobody is listening: the run reports and ends, and another run is created and finishes
    insertEvent('offline-end', 1, RESULT);
    repo.updateRun(db, 'offline-end', { status: 'finished', endedAt: 5000, numTurns: 1, costUsd: 0.1 });
    hub.publishLifecycle(repo.lifecycleOf(repo.getRun(db, 'offline-end') as never));
    makeRun('created-offline', 'lane', 'finished', 6000);
    repo.updateRun(db, 'created-offline', { endedAt: 7000 });
    insertEvent('created-offline', 1, RESULT);

    const second = await connect();
    await second.subscribe(useMissionStore.getState().lastSeqByRun());

    const state = useMissionStore.getState();
    // the known run is terminal, from the server's own record of it ...
    expect(state.runs['offline-end']?.status).toBe('finished');
    expect(state.runs['offline-end']?.endedAt).toBe(5000);
    // ... its events arrived too, so the trajectory is not missing its result
    expect(state.runs['offline-end']?.outcome).toEqual({ subtype: 'success', isError: false });
    // ... and the run created while offline is discovered rather than invisible until a reload
    expect(state.runs['created-offline']?.status).toBe('finished');
    expect(state.runsByLane.lane).toEqual(['offline-end', 'created-offline']);
    // The snapshot names the run and says how it ended, but not its prompt or directory. So the
    // client records it as a run it cannot yet describe, and asks about that run specifically —
    // rather than depending on the lane's run list happening to be newer than this snapshot.
    expect(state.runs['created-offline']?.prompt).toBe('');
    expect(state.unknownRuns).toEqual(['created-offline']);
    second.ws.close();
    await closed(second.ws);
  });

  /**
   * The interleaving that made the previous rule wrong: `runs.list` is generated *before* a run
   * exists and arrives *after* the snapshot that names it. Relying on that response for metadata
   * left the run a permanent placeholder — no prompt, no directory — with nothing to correct it.
   */
  it('recovers metadata for a run the lane list was generated too early to know about', async () => {
    makeRun('before-list', 'lane', 'finished', 4000);
    repo.updateRun(db, 'before-list', { endedAt: 4100 });
    const listedEarly = [
      {
        ...repo.lifecycleOf(repo.getRun(db, 'before-list') as never),
        prompt: 'the real prompt',
        cwd: '/tmp',
        browser: 'chrome' as const,
        resumable: false,
      },
    ];
    // ... and now a run the list above could not have contained
    makeRun('after-list', 'lane', 'running', 5000);

    const c = await connect();
    await c.subscribe({});
    // the snapshot is the authority on *what exists*
    expect(useMissionStore.getState().runs['after-list']?.status).toBe('running');

    // the older list lands afterwards: it must not remove the newer run from the lane, and it must
    // not take the newer run off the list of runs still to be described
    useMissionStore.getState().hydrateRuns(listedEarly);
    const afterStaleList = useMissionStore.getState();
    expect(afterStaleList.runsByLane.lane).toContain('after-list');
    expect(afterStaleList.runs['before-list']?.prompt).toBe('the real prompt');
    expect(afterStaleList.unknownRuns).not.toContain('before-list');
    expect(afterStaleList.unknownRuns).toContain('after-list');

    // which is what `useUnknownRuns` then asks for, one request for that one run
    useMissionStore.getState().hydrateRuns([
      {
        ...repo.lifecycleOf(repo.getRun(db, 'after-list') as never),
        prompt: 'after-list',
        cwd: '/tmp',
        browser: 'chrome' as const,
        resumable: false,
      },
    ]);
    const healed = useMissionStore.getState();
    expect(healed.runs['after-list']?.prompt).toBe('after-list');
    expect(healed.runs['after-list']?.cwd).toBe('/tmp');
    expect(healed.unknownRuns).not.toContain('after-list');
    c.ws.close();
    await closed(c.ws);
  });

  /**
   * The same ordering, the other way round: a REST response generated before a lifecycle change
   * must not undo what the socket has since reported.
   */
  it('does not let an older run list regress newer lifecycle state', async () => {
    makeRun('overtaken', 'lane', 'running', 9000);
    const staleDto = {
      ...repo.lifecycleOf(repo.getRun(db, 'overtaken') as never),
      prompt: 'overtaken',
      cwd: '/tmp',
      browser: 'off' as const,
      resumable: false,
    };
    repo.updateRun(db, 'overtaken', { status: 'finished', endedAt: 9500, costUsd: 0.25, numTurns: 3 });
    useMissionStore.getState().applyLifecycle(repo.lifecycleOf(repo.getRun(db, 'overtaken') as never));
    expect(useMissionStore.getState().runs.overtaken?.status).toBe('finished');

    useMissionStore.getState().hydrateRuns([staleDto]);

    const view = useMissionStore.getState().runs.overtaken;
    expect(view?.status).toBe('finished');
    expect(view?.endedAt).toBe(9500);
    expect(view?.numbers?.costUsd).toBe(0.25);
    expect(view?.origin).toBe('execution');
    // the metadata the socket never carries is still adopted from the older response
    expect(view?.prompt).toBe('overtaken');
  });

  it('recovers a run that failed without ever emitting an event', async () => {
    // A bad directory fails before the agent starts, so there is no event whose arrival could
    // carry the news: only the lifecycle says what happened.
    makeRun('no-events', 'lane', 'running', 8000);
    useMissionStore
      .getState()
      .reconcileLane('lane', [repo.lifecycleOf(repo.getRun(db, 'no-events') as never)]);
    expect(useMissionStore.getState().runs['no-events']?.status).toBe('running');

    const c = await connect();
    c.ws.close();
    await closed(c.ws);
    repo.updateRun(db, 'no-events', {
      status: 'failed',
      endedAt: 8100,
      errorMessage: '/bad/path is not a directory',
    });

    const again = await connect();
    await again.subscribe({ 'no-events': 0 });
    const view = useMissionStore.getState().runs['no-events'];
    expect(view?.status).toBe('failed');
    expect(view?.error?.message).toBe('/bad/path is not a directory');
    again.ws.close();
    await closed(again.ws);
  });

  it('does not duplicate or reorder anything when the same subscription is repeated', async () => {
    makeRun('repeat', 'lane', 'running', 9000);
    for (let seq = 1; seq <= 4; seq += 1)
      insertEvent(
        'repeat',
        seq,
        { type: 'system', subtype: 'thinking_tokens', estimated_tokens: seq },
        9000 + seq,
      );

    const c = await connect();
    await c.subscribe({ repeat: 0 });
    const after = useMissionStore.getState().runs.repeat;
    expect(after?.lastSeq).toBe(4);
    expect(after?.eventCount).toBe(4);

    // subscribing again from the same cursor sends nothing to apply twice ...
    await c.subscribe(useMissionStore.getState().lastSeqByRun());
    expect(useMissionStore.getState().runs.repeat?.eventCount).toBe(4);
    // ... and subscribing from the beginning again is still idempotent, because the store drops
    // any envelope at or below the sequence it has already applied
    await c.subscribe({ repeat: 0 });
    expect(useMissionStore.getState().runs.repeat?.eventCount).toBe(4);
    expect(useMissionStore.getState().runs.repeat?.lastSeq).toBe(4);
    expect(useMissionStore.getState().runsByLane.lane?.filter((id) => id === 'repeat')).toHaveLength(1);
    c.ws.close();
    await closed(c.ws);
  });

  it('never moves a terminal run back to running', async () => {
    makeRun('settled', 'lane', 'cancelled', 10_000);
    repo.updateRun(db, 'settled', { endedAt: 10_500 });
    const c = await connect();
    await c.subscribe({ settled: 0 });
    expect(useMissionStore.getState().runs.settled?.status).toBe('cancelled');

    // a stale lifecycle arriving late (a retried publish, an out-of-order frame) cannot revive it
    useMissionStore.getState().reconcileLane('lane', [
      {
        laneId: 'lane',
        runId: 'settled',
        origin: 'execution',
        status: 'running',
        startedAt: 10_000,
      },
    ]);
    expect(useMissionStore.getState().runs.settled?.status).toBe('cancelled');
    c.ws.close();
    await closed(c.ws);
  });
});
