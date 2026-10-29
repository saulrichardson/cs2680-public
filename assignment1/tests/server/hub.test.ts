import { createServer, type Server } from 'node:http';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import WebSocket from 'ws';
import type { WsClientMessage, WsServerMessage } from '@/core/types';
import { type Db, openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { Hub } from '@/server/ws/hub';

let server: Server;
let hub: Hub;
let db: Db;
let url: string;

// `next()` reads from a cursor over `messages` instead of only ever waiting on the *next*
// 'message' event: the server can send 'hello' (and other replies) synchronously enough after
// 'open' that both arrive in the same read, before test code gets a chance to register a waiter.
// A pure "wait for the next event" queue would then drop that already-buffered message; the
// cursor lets a late `next()` call pick up what already arrived.
function connect(target: string = url): Promise<{
  ws: WebSocket;
  messages: WsServerMessage[];
  next: () => Promise<WsServerMessage>;
}> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(target);
    const messages: WsServerMessage[] = [];
    const waiters: ((m: WsServerMessage) => void)[] = [];
    let cursor = 0;
    ws.on('message', (data) => {
      const m = JSON.parse(data.toString()) as WsServerMessage;
      messages.push(m);
      const waiter = waiters.shift();
      if (waiter) {
        cursor += 1;
        waiter(m);
      }
    });
    const next = (): Promise<WsServerMessage> => {
      if (cursor < messages.length) return Promise.resolve(messages[cursor++] as WsServerMessage);
      return new Promise((r) => waiters.push(r));
    };
    ws.on('open', () => resolve({ ws, messages, next }));
    ws.on('error', reject);
  });
}
const send = (ws: WebSocket, m: WsClientMessage) => ws.send(JSON.stringify(m));

/**
 * The next frame that is not a lane snapshot. Every subscribe now opens with one per lane (see
 * `Hub.subscribe`); the tests below are about what follows it, and one test asserts the snapshot
 * itself.
 */
async function nextPastSnapshot(next: () => Promise<WsServerMessage>): Promise<WsServerMessage> {
  let msg = await next();
  while (msg.kind === 'lane-state') msg = await next();
  return msg;
}

function waitFor(pred: () => boolean, ms = 5000): Promise<void> {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      if (pred()) resolve();
      else if (Date.now() - t0 > ms) reject(new Error('timeout'));
      else setTimeout(tick, 5);
    };
    tick();
  });
}

/**
 * Wire a hub into an HTTP server exactly as `server.ts` does — `/ws` upgrades go to the hub, any
 * other upgrade is dropped — and return the ws:// URL it listens on.
 */
async function listen(h: Hub): Promise<{ server: Server; url: string }> {
  const srv = createServer((_req, res) => res.end('ok'));
  srv.on('upgrade', (req, socket, head) => {
    if (new URL(req.url ?? '/', 'http://localhost').pathname === '/ws') h.handleUpgrade(req, socket, head);
    else socket.destroy();
  });
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', () => r()));
  const addr = srv.address();
  if (!addr || typeof addr === 'string') throw new Error('no address');
  return { server: srv, url: `ws://127.0.0.1:${addr.port}/ws` };
}

beforeAll(async () => {
  ({ db } = openDb(':memory:'));
  repo.createLane(db, { id: 'laneA', name: 'A', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
  repo.createLane(db, { id: 'laneB', name: 'B', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
  repo.createRun(db, {
    id: 'runA',
    laneId: 'laneA',
    prompt: 'p',
    effectiveCwd: '/tmp',
    permission: 'allowlist',
    origin: 'execution',
    status: 'running',
    startedAt: 1,
  });
  for (let i = 1; i <= 5; i += 1)
    repo.insertEvent(db, {
      runId: 'runA',
      seq: i,
      receivedAt: i,
      type: 'system',
      parentToolUseId: null,
      json: JSON.stringify({ type: 'system', subtype: 'thinking_tokens', n: i }),
    });
  hub = new Hub({ db, flushMs: 5 });
  ({ server, url } = await listen(hub));
});
afterAll(async () => {
  await hub.close();
  await new Promise<void>((r) => server.close(() => r()));
});

describe('Hub', () => {
  it('broadcasts session inventory changes without requiring a lane subscription', async () => {
    const first = await connect();
    const second = await connect();
    try {
      expect((await first.next()).kind).toBe('hello');
      expect((await second.next()).kind).toBe('hello');
      hub.publishLanesChanged();
      expect(await first.next()).toEqual({ kind: 'lanes-changed' });
      expect(await second.next()).toEqual({ kind: 'lanes-changed' });
    } finally {
      first.ws.close();
      second.ws.close();
      await waitFor(() => hub.clientCount() === 0);
    }
  });
  it('greets and rejects other upgrade paths', async () => {
    const c = await connect();
    const hello = c.messages[0] ?? (await c.next());
    expect(hello.kind).toBe('hello');
    expect(hub.clientCount()).toBe(1);
    await expect(
      new Promise((resolve, reject) => {
        const w = new WebSocket(url.replace('/ws', '/nope'));
        w.on('open', () => resolve('opened'));
        w.on('error', reject);
      }),
    ).rejects.toBeTruthy();
    c.ws.close();
  });

  it('backfills from the resume seq before live events, then batches live events', async () => {
    const c = await connect();
    await c.next(); // hello
    send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runA: 2 } });
    const backfill = await nextPastSnapshot(c.next);
    expect(backfill.kind).toBe('events');
    if (backfill.kind === 'events') expect(backfill.envelopes.map((e) => e.seq)).toEqual([3, 4, 5]);
    const ack = await c.next(); // subscribed, sent after the backfill flush
    expect(ack.kind).toBe('subscribed');

    hub.publishEvent({
      laneId: 'laneA',
      runId: 'runA',
      seq: 6,
      receivedAt: 6,
      event: { type: 'system', subtype: 'x' },
    });
    hub.publishEvent({
      laneId: 'laneA',
      runId: 'runA',
      seq: 7,
      receivedAt: 7,
      event: { type: 'system', subtype: 'y' },
    });
    hub.publishEvent({
      laneId: 'laneB',
      runId: 'runB',
      seq: 1,
      receivedAt: 1,
      event: { type: 'system', subtype: 'other-lane' },
    });
    const live = await c.next();
    expect(live.kind).toBe('events');
    if (live.kind === 'events') expect(live.envelopes.map((e) => e.seq)).toEqual([6, 7]);
    c.ws.close();
  });

  it('delivers lifecycle after pending events, in order', async () => {
    const c = await connect();
    await c.next();
    send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
    const ack = await nextPastSnapshot(c.next);
    expect(ack.kind).toBe('subscribed');
    hub.publishEvent({
      laneId: 'laneA',
      runId: 'runA',
      seq: 8,
      receivedAt: 8,
      event: { type: 'result', subtype: 'success' },
    });
    hub.publishLifecycle({
      laneId: 'laneA',
      runId: 'runA',
      origin: 'execution',
      status: 'finished',
      startedAt: 1,
      endedAt: 9,
    });
    const first = await c.next();
    const second = await c.next();
    expect(first.kind).toBe('events');
    expect(second.kind).toBe('run');
    if (second.kind === 'run') expect(second.lifecycle.status).toBe('finished');
    c.ws.close();
  });

  it("opens a subscription with the lane's current runs, before any backfill", async () => {
    const c = await connect();
    await c.next(); // hello
    send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runA: 5 } });
    const snapshot = await c.next();
    expect(snapshot.kind).toBe('lane-state');
    if (snapshot.kind === 'lane-state') {
      expect(snapshot.laneId).toBe('laneA');
      // every run the lane holds, with what the server says about it — not only the ones the
      // client asked about, which is how a run created while it was away is discovered
      expect(snapshot.runs.map((r) => r.runId)).toContain('runA');
      expect(snapshot.runs.find((r) => r.runId === 'runA')).toMatchObject({
        laneId: 'laneA',
        status: 'running',
        origin: 'execution',
      });
    }
    let msg = await c.next();
    while (msg.kind === 'lane-state' || msg.kind === 'events') msg = await c.next();
    expect(msg.kind).toBe('subscribed');
    c.ws.close();
  });

  it('drops an oversized frame without taking the server down', async () => {
    const c = await connect();
    await c.next(); // hello
    const closedWith = new Promise<number>((r) => c.ws.on('close', (code) => r(code)));
    // Clients only ever send a subscription, which is bytes per run. Anything past the cap is
    // refused by the protocol layer rather than buffered and parsed.
    c.ws.send(JSON.stringify({ kind: 'subscribe', laneIds: ['laneA'], pad: 'x'.repeat(2 * 1024 * 1024) }));
    expect(await closedWith).toBe(1009); // "message too big"

    // and the hub is still serving everyone else
    const other = await connect();
    await other.next();
    send(other.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
    const ack = await nextPastSnapshot(other.next);
    expect(ack.kind).toBe('subscribed');
    other.ws.close();
  });

  it('unsubscribe stops delivery', async () => {
    const c = await connect();
    await c.next();
    send(c.ws, { kind: 'subscribe', laneIds: ['laneB'], resume: {} });
    const subscribed = await nextPastSnapshot(c.next);
    expect(subscribed.kind).toBe('subscribed');
    send(c.ws, { kind: 'unsubscribe', laneIds: ['laneB'] });
    const unsubscribed = await c.next();
    expect(unsubscribed.kind).toBe('unsubscribed');
    hub.publishEvent({ laneId: 'laneB', runId: 'runB', seq: 2, receivedAt: 2, event: { type: 'system' } });
    // No ack is sent for a publish a client isn't subscribed to, so there's nothing to await here;
    // this wait just gives a (deliberately unsubscribed-from) flush time to happen if it wrongly did.
    await new Promise((r) => setTimeout(r, 30));
    expect(c.messages.filter((m) => m.kind === 'events')).toHaveLength(0);
    c.ws.close();
  });

  it('ignores malformed frames instead of crashing the connection', async () => {
    const c = await connect();
    await c.next(); // hello

    // Previously, a JSON-valid frame missing `laneIds` threw "undefined is not iterable"
    // synchronously inside the 'message' listener (`for (const id of msg.laneIds)`), an uncaught
    // exception that could take the whole server down. `{ kind: 'unsubscribe' }` alone still
    // produces its (now-defaulted, empty-laneIds) ack; the rest below produce no frame at all.
    c.ws.send(JSON.stringify({ kind: 'unsubscribe' }));
    c.ws.send('not json at all');
    c.ws.send(JSON.stringify(42));
    c.ws.send(JSON.stringify({ kind: 'nonsense' }));

    const unsubAck = await c.next();
    expect(unsubAck.kind).toBe('unsubscribed');
    if (unsubAck.kind === 'unsubscribed') expect(unsubAck.laneIds).toEqual([]);

    // The connection, and the hub, must still be alive and answer a well-formed request afterwards.
    send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
    const ack = await nextPastSnapshot(c.next);
    expect(ack.kind).toBe('subscribed');
    if (ack.kind === 'subscribed') expect(ack.laneIds).toEqual(['laneA']);
    expect(hub.clientCount()).toBeGreaterThan(0);
    c.ws.close();
  });

  it('backfills a malformed event row as an unparsed envelope instead of throwing out of subscribe', async () => {
    repo.createRun(db, {
      id: 'runCorrupt',
      laneId: 'laneA',
      prompt: 'p',
      effectiveCwd: '/tmp',
      permission: 'allowlist',
      origin: 'execution',
      status: 'running',
      startedAt: 1,
    });
    repo.insertEvent(db, {
      runId: 'runCorrupt',
      seq: 1,
      receivedAt: 1,
      type: 'system',
      parentToolUseId: null,
      json: JSON.stringify({ type: 'system', subtype: 'ok1' }),
    });
    // Rows hold the agent's original text, so a malformed line is stored as-is (see A7). Reading it
    // back must not throw out of subscribe(); it comes through as the same `unparsed` event the live
    // stream produced, so the client sees the line rather than a hole in the sequence.
    repo.insertEvent(db, {
      runId: 'runCorrupt',
      seq: 2,
      receivedAt: 2,
      type: 'unparsed',
      parentToolUseId: null,
      json: 'not valid json {',
    });
    repo.insertEvent(db, {
      runId: 'runCorrupt',
      seq: 3,
      receivedAt: 3,
      type: 'system',
      parentToolUseId: null,
      json: JSON.stringify({ type: 'system', subtype: 'ok3' }),
    });

    const c = await connect();
    await c.next(); // hello
    send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runCorrupt: 0 } });
    const backfill = await nextPastSnapshot(c.next);
    expect(backfill.kind).toBe('events');
    if (backfill.kind === 'events') {
      expect(backfill.envelopes.map((e) => e.seq)).toEqual([1, 2, 3]);
      expect(backfill.envelopes[1]?.event).toMatchObject({ type: 'unparsed', raw: 'not valid json {' });
    }
    const ack = await c.next();
    expect(ack.kind).toBe('subscribed');
    c.ws.close();
  });

  it('re-subscribing to an in-flight run replaces buffered live envelopes with a clean backfill', async () => {
    // A dedicated hub with a longer flush window: publishEvent only buffers envelopes in memory,
    // and this scenario needs that buffered publish to still be sitting in `pending`, unflushed,
    // when the resubscribe is processed. The shared hub's 5ms flush timer is too tight to rely on
    // for that race, so use a separate hub/server pair per the controller's ruling.
    const racyHub = new Hub({ db, flushMs: 50 });
    const { server: racyServer, url: racyUrl } = await listen(racyHub);

    try {
      const c = await connect(racyUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
      const firstAck = await nextPastSnapshot(c.next);
      expect(firstAck.kind).toBe('subscribed');

      // The process manager persists each event to SQLite before broadcasting it; mirror that
      // here so the eventual backfill query already contains everything buffered below.
      repo.insertEvent(db, {
        runId: 'runA',
        seq: 6,
        receivedAt: 6,
        type: 'system',
        parentToolUseId: null,
        json: JSON.stringify({ type: 'system', subtype: 'x' }),
      });
      repo.insertEvent(db, {
        runId: 'runA',
        seq: 7,
        receivedAt: 7,
        type: 'system',
        parentToolUseId: null,
        json: JSON.stringify({ type: 'system', subtype: 'y' }),
      });
      racyHub.publishEvent({
        laneId: 'laneA',
        runId: 'runA',
        seq: 6,
        receivedAt: 6,
        event: { type: 'system', subtype: 'x' },
      });
      racyHub.publishEvent({
        laneId: 'laneA',
        runId: 'runA',
        seq: 7,
        receivedAt: 7,
        event: { type: 'system', subtype: 'y' },
      });
      // Immediately (well within the 50ms flush window) resubscribe with a resume seq that
      // straddles the still-buffered, not-yet-flushed live envelopes above.
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runA: 3 } });

      const seqs: number[] = [];
      let msg = await c.next();
      while (msg.kind !== 'subscribed') {
        if (msg.kind === 'events') seqs.push(...msg.envelopes.map((e) => e.seq));
        msg = await c.next();
      }
      expect(seqs).toEqual([4, 5, 6, 7]);
      c.ws.close();
    } finally {
      await racyHub.close();
      await new Promise<void>((r) => racyServer.close(() => r()));
    }
  });

  it('pings every client and terminates one that stops answering', async () => {
    // A short beat stands in for the 30 s production one. `ws` answers a ping automatically, so a
    // client that is *reading* stays alive across many beats; pausing its socket makes it look
    // exactly like a connection that died without a close frame.
    const beatHub = new Hub({ db, flushMs: 5, pingMs: 25 });
    const { server: beatServer, url: beatUrl } = await listen(beatHub);
    try {
      const healthy = await connect(beatUrl);
      const silent = await connect(beatUrl);
      expect(beatHub.clientCount()).toBe(2);
      silent.ws.pause();
      const closed = new Promise<void>((r) => silent.ws.on('close', () => r()));
      await waitFor(() => beatHub.clientCount() === 1);
      // the one still reading survives several beats and can still be used
      await new Promise((r) => setTimeout(r, 80));
      expect(beatHub.clientCount()).toBe(1);
      send(healthy.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
      let msg = await healthy.next();
      while (msg.kind !== 'subscribed') msg = await healthy.next();
      healthy.ws.close();
      silent.ws.resume();
      await closed;
    } finally {
      await beatHub.close();
      await new Promise<void>((r) => beatServer.close(() => r()));
    }
  });

  /**
   * A history larger than the socket's outgoing cap must still arrive, in full and in order.
   *
   * Sending it page after page without yielding meant the socket could never drain, so the hub's
   * own writes pushed `bufferedAmount` past the cap and it disconnected a perfectly healthy client
   * half way through its own history — 2,000 of 4,000 events and no `subscribed` ack, in the
   * reproduction. The backfill now waits for the socket between pages.
   */
  it('delivers a backlog larger than the outgoing cap to a healthy client, in order', async () => {
    // a deliberately tiny cap, so the same behaviour is exercised without moving megabytes
    const pacedHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 32 * 1024, stallMs: 10_000 });
    const { server: pacedServer, url: pacedUrl } = await listen(pacedHub);
    try {
      repo.createRun(db, {
        id: 'runBig',
        laneId: 'laneA',
        prompt: 'p',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        origin: 'execution',
        status: 'finished',
        startedAt: 1,
      });
      const payload = JSON.stringify({ type: 'system', subtype: 'big', pad: 'x'.repeat(2048) });
      const total = 1500;
      for (let seq = 1; seq <= total; seq += 1)
        repo.insertEvent(db, {
          runId: 'runBig',
          seq,
          receivedAt: seq,
          type: 'system',
          parentToolUseId: null,
          json: payload,
        });

      const c = await connect(pacedUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runBig: 0 } });

      const seqs: number[] = [];
      let msg = await c.next();
      while (msg.kind !== 'subscribed') {
        if (msg.kind === 'events') for (const e of msg.envelopes) if (e.runId === 'runBig') seqs.push(e.seq);
        msg = await c.next();
      }
      // every event, once, in order, and the subscription was acknowledged rather than closed
      expect(seqs).toHaveLength(total);
      expect(seqs).toEqual(Array.from({ length: total }, (_, i) => i + 1));
      expect(pacedHub.clientCount()).toBe(1);
      c.ws.close();
    } finally {
      await pacedHub.close();
      await new Promise<void>((r) => pacedServer.close(() => r()));
    }
  }, 30_000);

  it('keeps live events behind the history they follow', async () => {
    const pacedHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 32 * 1024, stallMs: 10_000 });
    const { server: pacedServer, url: pacedUrl } = await listen(pacedHub);
    try {
      repo.createRun(db, {
        id: 'runOrder',
        laneId: 'laneA',
        prompt: 'p',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        origin: 'execution',
        status: 'running',
        startedAt: 1,
      });
      const payload = JSON.stringify({ type: 'system', subtype: 'hist', pad: 'y'.repeat(2048) });
      for (let seq = 1; seq <= 800; seq += 1)
        repo.insertEvent(db, {
          runId: 'runOrder',
          seq,
          receivedAt: seq,
          type: 'system',
          parentToolUseId: null,
          json: payload,
        });

      const c = await connect(pacedUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: { runOrder: 0 } });

      // A live event published while the history is still going out. It is newer than everything
      // stored, so it must arrive *after* all of it, not in the middle.
      await new Promise((r) => setTimeout(r, 15));
      repo.insertEvent(db, {
        runId: 'runOrder',
        seq: 801,
        receivedAt: 801,
        type: 'system',
        parentToolUseId: null,
        json: JSON.stringify({ type: 'system', subtype: 'live' }),
      });
      pacedHub.publishEvent({
        laneId: 'laneA',
        runId: 'runOrder',
        seq: 801,
        receivedAt: 801,
        event: { type: 'system', subtype: 'live' },
      });

      const seqs: number[] = [];
      let msg = await c.next();
      while (msg.kind !== 'subscribed') {
        if (msg.kind === 'events')
          for (const e of msg.envelopes) if (e.runId === 'runOrder') seqs.push(e.seq);
        msg = await c.next();
      }
      // the live event may arrive inside the backfill (it was persisted before the last page read)
      // or just after the ack; either way the sequence the client sees only ever increases
      for (let i = 1; i < seqs.length; i += 1) expect(seqs[i]).toBeGreaterThan(seqs[i - 1] as number);
      expect(seqs[0]).toBe(1);
      c.ws.close();
    } finally {
      await pacedHub.close();
      await new Promise<void>((r) => pacedServer.close(() => r()));
    }
  }, 30_000);

  /**
   * A new subscription must not lose what happens while it is still being served.
   *
   * The backfill yields between pages. Membership used to be granted only after it finished, so
   * every event and lifecycle produced during those yields went to nobody — a short run's final
   * event and its terminal status vanished while a long run in the same lane was still being sent,
   * leaving the client showing a finished run as running. This is a *new* socket, which is the case
   * a resubscribe test cannot reach.
   */
  it('captures live events and lifecycles produced while a new subscription is still backfilling', async () => {
    const pacedHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 32 * 1024, stallMs: 10_000 });
    const { server: pacedServer, url: pacedUrl } = await listen(pacedHub);
    try {
      for (const id of ['handoffShort', 'handoffLong'])
        repo.createRun(db, {
          id,
          laneId: 'laneA',
          prompt: id,
          effectiveCwd: '/tmp',
          permission: 'allowlist',
          origin: 'execution',
          status: 'running',
          startedAt: 1,
        });
      const payload = JSON.stringify({ type: 'system', subtype: 'p', pad: 'z'.repeat(2048) });
      const write = (runId: string, seq: number) =>
        repo.insertEvent(db, {
          runId,
          seq,
          receivedAt: seq,
          type: 'system',
          parentToolUseId: null,
          json: payload,
        });
      write('handoffShort', 1);
      for (let seq = 1; seq <= 1200; seq += 1) write('handoffLong', seq);

      const c = await connect(pacedUrl);
      await c.next(); // hello
      send(c.ws, {
        kind: 'subscribe',
        laneIds: ['laneA'],
        resume: { handoffShort: 0, handoffLong: 0 },
      });

      // The short run finishes while the long one's history is still going out.
      let injected = false;
      const shortSeqs: number[] = [];
      let shortStatus = 'unknown';
      let msg = await c.next();
      while (msg.kind !== 'subscribed') {
        if (msg.kind === 'lane-state')
          shortStatus = msg.runs.find((r) => r.runId === 'handoffShort')?.status ?? shortStatus;
        if (msg.kind === 'run' && msg.lifecycle.runId === 'handoffShort') shortStatus = msg.lifecycle.status;
        if (msg.kind === 'events') {
          for (const e of msg.envelopes) if (e.runId === 'handoffShort') shortSeqs.push(e.seq);
          if (!injected && shortSeqs.length > 0) {
            injected = true;
            write('handoffShort', 2);
            repo.updateRun(db, 'handoffShort', { status: 'finished', endedAt: 2 });
            pacedHub.publishEvent({
              laneId: 'laneA',
              runId: 'handoffShort',
              seq: 2,
              receivedAt: 2,
              event: { type: 'system', subtype: 'p' },
            });
            pacedHub.publishLifecycle(repo.lifecycleOf(repo.getRun(db, 'handoffShort') as never));
          }
        }
        msg = await c.next();
      }
      // one more beat for anything queued behind the ack
      await new Promise((r) => setTimeout(r, 50));
      for (const m of c.messages)
        if (m.kind === 'run' && m.lifecycle.runId === 'handoffShort') shortStatus = m.lifecycle.status;
      const seen = c.messages
        .flatMap((m) => (m.kind === 'events' ? m.envelopes : []))
        .filter((e) => e.runId === 'handoffShort')
        .map((e) => e.seq);

      expect(injected).toBe(true);
      expect(seen).toEqual([1, 2]);
      expect(shortStatus).toBe('finished');
      c.ws.close();
    } finally {
      await pacedHub.close();
      await new Promise<void>((r) => pacedServer.close(() => r()));
    }
  }, 30_000);

  /**
   * Buffered work needs an owner. A flush that stopped at the cap used to leave the rest queued and
   * schedule nothing, so a run's last event and terminal lifecycle could sit there for ever if
   * nothing else was ever published — which is exactly the moment a run ends.
   */
  it('delivers work left over from a backpressured flush without another publish', async () => {
    const pacedHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 4 * 1024, stallMs: 10_000 });
    const { server: pacedServer, url: pacedUrl } = await listen(pacedHub);
    try {
      const c = await connect(pacedUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
      const ack = await nextPastSnapshot(c.next);
      expect(ack.kind).toBe('subscribed');

      // One burst, big enough that the socket goes over the (tiny) cap part-way through, then
      // nothing else is ever published. The last item must still arrive.
      for (let i = 0; i < 1500; i += 1)
        pacedHub.publishEvent({
          laneId: 'laneA',
          runId: 'runLeftover',
          seq: i + 1,
          receivedAt: i,
          event: { type: 'system', subtype: 'x'.repeat(512) },
        });
      pacedHub.publishLifecycle({
        laneId: 'laneA',
        runId: 'runLeftover',
        origin: 'execution',
        status: 'finished',
        startedAt: 1,
        endedAt: 2,
      });

      await waitFor(() => c.messages.some((m) => m.kind === 'run' && m.lifecycle.runId === 'runLeftover'));
      const seqs = c.messages
        .flatMap((m) => (m.kind === 'events' ? m.envelopes : []))
        .filter((e) => e.runId === 'runLeftover')
        .map((e) => e.seq);
      expect(seqs).toEqual(Array.from({ length: 1500 }, (_, i) => i + 1));
      c.ws.close();
    } finally {
      await pacedHub.close();
      await new Promise<void>((r) => pacedServer.close(() => r()));
    }
  }, 30_000);

  it('does not resurrect a lane that was unsubscribed while its subscribe was still running', async () => {
    const pacedHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 32 * 1024, stallMs: 10_000 });
    const { server: pacedServer, url: pacedUrl } = await listen(pacedHub);
    try {
      repo.createRun(db, {
        id: 'runUnsub',
        laneId: 'laneB',
        prompt: 'p',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        origin: 'execution',
        status: 'running',
        startedAt: 1,
      });
      const payload = JSON.stringify({ type: 'system', subtype: 'u', pad: 'q'.repeat(2048) });
      for (let seq = 1; seq <= 1200; seq += 1)
        repo.insertEvent(db, {
          runId: 'runUnsub',
          seq,
          receivedAt: seq,
          type: 'system',
          parentToolUseId: null,
          json: payload,
        });

      const c = await connect(pacedUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneB'], resume: { runUnsub: 0 } });
      // sent while the backfill is still yielding; it is queued behind it, not applied under it
      send(c.ws, { kind: 'unsubscribe', laneIds: ['laneB'] });

      let msg = await c.next();
      while (msg.kind !== 'unsubscribed') msg = await c.next();
      const before = c.messages.length;
      pacedHub.publishEvent({
        laneId: 'laneB',
        runId: 'runUnsub',
        seq: 5000,
        receivedAt: 1,
        event: { type: 'system', subtype: 'after' },
      });
      await new Promise((r) => setTimeout(r, 40));
      expect(c.messages.length).toBe(before);
      c.ws.close();
    } finally {
      await pacedHub.close();
      await new Promise<void>((r) => pacedServer.close(() => r()));
    }
  }, 30_000);

  /**
   * The other side of the same rule: a socket that is over the cap and *stays* there is a stalled
   * connection, not a busy one, and is dropped rather than buffered for. `stallMs: 0` makes "still
   * over the cap on the next flush" immediately terminal; production allows 30 s.
   */
  it('drops a socket that stays full, rather than one that is merely busy', async () => {
    const stallHub = new Hub({ db, flushMs: 5, maxBufferedBytes: 32 * 1024, stallMs: 0 });
    const { server: stallServer, url: stallUrl } = await listen(stallHub);
    try {
      const c = await connect(stallUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
      const ack = await nextPastSnapshot(c.next);
      expect(ack.kind).toBe('subscribed');
      const code = new Promise<number>((r) => c.ws.on('close', (n) => r(n)));

      // A client that has genuinely stopped reading. Each burst is published synchronously, so one
      // flush really does hand the socket more than it can pass on; the next burst is what finds it
      // still full. (The check asks "may this socket be handed more?", so it only runs when there
      // is something to hand it — a stalled client with no further traffic costs nothing more and
      // is collected by the heartbeat instead.)
      c.ws.pause();
      for (let round = 0; round < 3; round += 1) {
        for (let i = 0; i < 700; i += 1)
          stallHub.publishEvent({
            laneId: 'laneA',
            runId: 'runStall',
            seq: round * 700 + i + 1,
            receivedAt: i,
            event: { type: 'system', subtype: 'x'.repeat(4096) },
          });
        await new Promise((r) => setTimeout(r, 20));
      }

      // The server lets go first. The close frame itself can only arrive once the client starts
      // reading again — which is the whole point of it being stalled.
      await waitFor(() => stallHub.clientCount() === 0);
      c.ws.resume();
      expect(await code).toBe(1008);
    } finally {
      await stallHub.close();
      await new Promise<void>((r) => stallServer.close(() => r()));
    }
  }, 30_000);

  it('closes a client that falls too far behind with 1008 instead of buffering forever', async () => {
    // A long flush window keeps everything in `pending`, which is the state a client that has
    // stopped reading puts the hub in for real.
    const slowHub = new Hub({ db, flushMs: 60_000 });
    const { server: slowServer, url: slowUrl } = await listen(slowHub);
    try {
      const c = await connect(slowUrl);
      await c.next(); // hello
      send(c.ws, { kind: 'subscribe', laneIds: ['laneA'], resume: {} });
      const ack = await nextPastSnapshot(c.next);
      expect(ack.kind).toBe('subscribed');
      const code = new Promise<number>((r) => c.ws.on('close', (n) => r(n)));
      for (let i = 0; i < 20_001; i += 1)
        slowHub.publishEvent({
          laneId: 'laneA',
          runId: 'runFlood',
          seq: i + 1,
          receivedAt: i,
          event: { type: 'system', subtype: 'flood' },
        });
      expect(slowHub.clientCount()).toBe(0);
      expect(await code).toBe(1008);
    } finally {
      await slowHub.close();
      await new Promise<void>((r) => slowServer.close(() => r()));
    }
  });
});
