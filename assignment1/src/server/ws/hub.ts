import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocket, WebSocketServer } from 'ws';
import type { Envelope, RunLifecycle, WsClientMessage, WsServerMessage } from '@/core/types';
import type { Db } from '@/server/db';
import * as repo from '@/server/db/repo';

/**
 * The slice of the `Hub` that the process manager and the replayer need: somewhere to put a run's
 * events and its lifecycle. Declared as an interface so those two never depend on the WebSocket
 * server itself, and so a test can hand them a recorder instead.
 */
export interface HubLike {
  publishEvent(env: Envelope): void;
  publishLifecycle(lc: RunLifecycle): void;
}

/**
 * One thing waiting to go out to a client, in the order it was produced.
 *
 * Events and lifecycles share a single queue on purpose. They used to travel by different routes —
 * events through `pending`, lifecycles sent straight away after a flush — which worked only while
 * nothing could interrupt a flush. Once a backfill could yield, a lifecycle could overtake the
 * history it describes, and an event published during those yields could be dropped entirely.
 */
type Outbound = { kind: 'event'; env: Envelope } | { kind: 'run'; lifecycle: RunLifecycle };

interface ClientState {
  ws: WebSocket;
  lanes: Set<string>;
  /** Everything waiting for this client, in order. Bounded by `MAX_PENDING`. */
  pending: Outbound[];
  /** Answered the last heartbeat ping. A client that misses one is dropped on the next tick. */
  isAlive: boolean;
  /**
   * Subscriptions are served one at a time, in the order they arrived. A subscription yields to the
   * event loop while it backfills, so without this a second frame could interleave its pages with
   * the first's.
   */
  work: Promise<void>;
  /**
   * A backfill is in progress, so live envelopes stay in `pending` instead of being flushed: they
   * belong *after* the history being sent, and flushing them now would deliver a run's events out
   * of order.
   */
  backfilling: number;
  /** When this socket first went over `MAX_BUFFERED_BYTES` without coming back under it. */
  stalledSince: number | null;
}

/**
 * How many envelopes may wait for one client before we give up on it. A browser that stops reading
 * (a suspended tab, a stalled connection) would otherwise grow this buffer without bound while a
 * fan-out of live agents streams into it. 20 000 is far above any real backlog — the flush timer
 * empties it every few milliseconds — so reaching it means the socket, not the load, is the problem.
 */
const MAX_PENDING = 20_000;

/**
 * How many bytes may sit unsent in one socket's own buffer before we stop handing it more.
 *
 * `MAX_PENDING` bounds the envelopes waiting to be *handed to* the socket, and that array is
 * emptied on every flush — so a client that has stopped reading never trips it, while `ws` quietly
 * accumulates everything we handed over. That buffer is the one that actually grows with a stalled
 * connection, so it is the one worth measuring.
 *
 * Crossing it is not by itself evidence of a bad client: a healthy socket cannot drain at all while
 * synchronous code keeps writing to it, which is exactly what a large backfill used to do to
 * itself. Above this mark the backfill waits for the socket instead (see `drain`); only a client
 * that is *still* above it after `STALL_MS` is dropped.
 */
const MAX_BUFFERED_BYTES = 8 * 1024 * 1024;

/** How long a socket may sit above `MAX_BUFFERED_BYTES` before it counts as stalled rather than busy. */
const STALL_MS = 30_000;

/** How often a waiting backfill re-checks whether its socket has drained. */
const DRAIN_POLL_MS = 5;

/**
 * The largest frame a client may send. Clients only ever send `subscribe` and `unsubscribe`, whose
 * size is a handful of bytes per run being resumed, so this is orders of magnitude above anything
 * legitimate while keeping an unbounded frame from being buffered before it is even parsed.
 */
const MAX_FRAME_BYTES = 1024 * 1024;

/** How many stored events are read and sent at a time while backfilling a reconnecting client. */
const BACKFILL_PAGE = 500;

/** Heartbeat period. A socket that has not ponged by the next tick is terminated. */
const PING_MS = 30_000;

/** How long `close` waits for sockets to close politely before terminating them. */
const CLOSE_GRACE_MS = 1000;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function parseLaneIds(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

function parseResume(v: unknown): Record<string, number> {
  if (!isPlainObject(v)) return {};
  const out: Record<string, number> = {};
  for (const [runId, lastSeq] of Object.entries(v)) {
    if (typeof lastSeq === 'number' && Number.isFinite(lastSeq)) out[runId] = lastSeq;
  }
  return out;
}

/** Defensively reshape an arbitrary parsed JSON value into a well-formed client message, or null if it isn't one. */
function parseClientMessage(raw: unknown): WsClientMessage | null {
  if (!isPlainObject(raw) || typeof raw.kind !== 'string') return null;
  if (raw.kind === 'subscribe') {
    return { kind: 'subscribe', laneIds: parseLaneIds(raw.laneIds), resume: parseResume(raw.resume) };
  }
  if (raw.kind === 'unsubscribe') return { kind: 'unsubscribe', laneIds: parseLaneIds(raw.laneIds) };
  return null;
}

export class Hub implements HubLike {
  private readonly wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME_BYTES });
  private readonly clients = new Set<ClientState>();
  private flushTimer: NodeJS.Timeout | null = null;
  private readonly flushMs: number;
  private readonly maxBufferedBytes: number;
  private readonly stallMs: number;
  private readonly pingTimer: NodeJS.Timeout;

  constructor(
    private readonly deps: {
      db: Db;
      flushMs?: number;
      pingMs?: number;
      /** Overridable so a test can cross the mark without moving megabytes. */
      maxBufferedBytes?: number;
      stallMs?: number;
    },
  ) {
    this.flushMs = deps.flushMs ?? 16;
    this.maxBufferedBytes = deps.maxBufferedBytes ?? MAX_BUFFERED_BYTES;
    this.stallMs = deps.stallMs ?? STALL_MS;
    this.wss.on('connection', (ws) => this.onConnection(ws));
    // A TCP connection that dies without a close frame (laptop lid, dropped wifi, a proxy timing
    // the socket out) leaves the server holding a client that will never read again: it keeps
    // buffering envelopes for it and keeps counting it as connected. Ping every client on a fixed
    // beat and drop the ones that did not pong before the next one.
    this.pingTimer = setInterval(() => this.heartbeat(), deps.pingMs ?? PING_MS);
    this.pingTimer.unref?.();
  }

  handleUpgrade(req: IncomingMessage, socket: Duplex, head: Buffer): void {
    this.wss.handleUpgrade(req, socket, head, (ws) => this.wss.emit('connection', ws, req));
  }

  clientCount(): number {
    return this.clients.size;
  }

  publishEvent(env: Envelope): void {
    this.enqueue(env.laneId, { kind: 'event', env });
  }

  publishLifecycle(lc: RunLifecycle): void {
    this.enqueue(lc.laneId, { kind: 'run', lifecycle: lc });
  }

  /** Inventory changes reach every tab, including those not subscribed to a newly created lane. */
  publishLanesChanged(): void {
    for (const client of this.clients) this.send(client.ws, { kind: 'lanes-changed' });
  }

  /** Queue one outbound item for every client subscribed to that lane, and ask for a flush. */
  private enqueue(laneId: string, item: Outbound): void {
    for (const c of this.clients) {
      if (!c.lanes.has(laneId)) continue;
      c.pending.push(item);
      if (c.pending.length > MAX_PENDING) this.dropSlowClient(c, `${MAX_PENDING} messages behind`);
    }
    this.scheduleFlush();
  }

  /** Is this socket holding more than we are willing to hand it before it reads some of it? */
  private overBuffered(c: ClientState): boolean {
    return c.ws.bufferedAmount > this.maxBufferedBytes;
  }

  /**
   * Wait until this socket has drained enough to be handed more, yielding to the event loop so it
   * actually can. Answers `false` when the client is gone or has stopped reading altogether.
   *
   * This is what separates a busy client from a broken one. A backfill that writes page after page
   * synchronously never lets the socket flush, so its own writes push `bufferedAmount` past the cap
   * and the hub used to disconnect a perfectly healthy browser part-way through its history.
   */
  private async drain(c: ClientState): Promise<boolean> {
    if (!this.clients.has(c) || c.ws.readyState !== WebSocket.OPEN) return false;
    if (!this.overBuffered(c)) {
      // Still yield once per page, so the socket gets a turn and live publishing is not starved.
      await new Promise((resolve) => setImmediate(resolve));
      return this.clients.has(c) && c.ws.readyState === WebSocket.OPEN;
    }
    const deadline = Date.now() + this.stallMs;
    while (this.overBuffered(c)) {
      if (!this.clients.has(c) || c.ws.readyState !== WebSocket.OPEN) return false;
      if (Date.now() > deadline) {
        this.dropSlowClient(c, `a socket buffer that has not drained in ${this.stallMs} ms`);
        return false;
      }
      await new Promise((resolve) => setTimeout(resolve, DRAIN_POLL_MS));
    }
    return this.clients.has(c) && c.ws.readyState === WebSocket.OPEN;
  }

  async close(): Promise<void> {
    if (this.flushTimer) clearTimeout(this.flushTimer);
    // ... and forget it: a stale handle would make `scheduleFlush` think a flush is already pending,
    // so a hub that is reused (or published to during shutdown) would silently stop flushing.
    this.flushTimer = null;
    clearInterval(this.pingTimer);
    // `wss.close` answers when every client has closed, and a socket whose peer has stopped reading
    // may never complete the handshake — which would hang the whole server's shutdown on one
    // stalled laptop. Ask politely, then insist.
    const done = new Promise<void>((resolve) => this.wss.close(() => resolve()));
    for (const c of this.clients) c.ws.close();
    const insist = setTimeout(() => {
      for (const c of this.clients) c.ws.terminate();
    }, CLOSE_GRACE_MS);
    await done;
    clearTimeout(insist);
    this.clients.clear();
  }

  /** One beat: drop whoever missed the last ping, then ping everyone still here. */
  private heartbeat(): void {
    for (const c of [...this.clients]) {
      if (!c.isAlive) {
        this.clients.delete(c);
        c.pending = [];
        c.ws.terminate();
        continue;
      }
      c.isAlive = false;
      try {
        c.ws.ping();
      } catch {
        // a socket that cannot even be pinged is already gone; the next tick collects it
      }
    }
  }

  /**
   * A client that is not draining its own backlog is no longer worth streaming to: close it with
   * 1008 and let its reconnect resume by seq, which backfills from the database in order instead of
   * replaying a stale in-memory pile.
   */
  private dropSlowClient(c: ClientState, why: string): void {
    if (!this.clients.delete(c)) return;
    c.pending = [];
    console.warn(`hub: closing a client with ${why}`);
    c.ws.close(1008, 'client too slow');
  }

  private onConnection(ws: WebSocket): void {
    const state: ClientState = {
      ws,
      lanes: new Set(),
      pending: [],
      isAlive: true,
      work: Promise.resolve(),
      backfilling: 0,
      stalledSince: null,
    };
    this.clients.add(state);
    this.send(ws, { kind: 'hello', serverTime: Date.now() });
    ws.on('pong', () => {
      state.isAlive = true;
    });
    ws.on('message', (data) => {
      try {
        const msg = parseClientMessage(JSON.parse(data.toString()));
        if (!msg) return;
        // One at a time, in arrival order. A subscription yields while it backfills, and the gap
        // recovery in `SocketBridge` can send another before the first has finished — and an
        // unsubscribe that overtook a subscribe still in flight would be undone by it.
        state.work = state.work.then(() =>
          (msg.kind === 'subscribe'
            ? this.subscribe(state, msg.laneIds, msg.resume)
            : Promise.resolve(this.unsubscribe(state, msg.laneIds))
          ).catch((err) => console.warn(`hub: ${msg.kind} failed`, err)),
        );
      } catch (err) {
        console.warn('hub: dropping malformed frame', err);
      }
    });
    ws.on('close', () => this.clients.delete(state));
    ws.on('error', () => this.clients.delete(state));
  }

  /**
   * Answer a subscription with everything the client needs to be correct again.
   *
   * Two different things are recovered here, and a reconnect needs both:
   *
   * - **what exists and how it ended** — the lane's runs and their current lifecycle, straight from
   *   the database. Events cannot carry this: a run that failed before emitting anything has none,
   *   a run's terminal status is set by the server rather than inferred from its `result`, and a run
   *   created while the client was away is not in the client's cursor map at all.
   * - **the events it has not seen** — backfilled from each cursor it does send, in order.
   *
   * The snapshot is written synchronously, so it always precedes the history it describes. The
   * history itself yields between pages (see `drain`), which is why live envelopes are held in
   * `pending` for the duration: they belong after it, and `flushClient` will not send them until
   * the backfill has caught the database up to the moment it finishes.
   */
  private async subscribe(
    state: ClientState,
    laneIds: string[],
    resume: Record<string, number>,
  ): Promise<void> {
    const wanted = new Set(laneIds);
    // Membership first, before anything can yield. The backfill below hands the event loop back
    // between pages, and every live event and lifecycle produced during those yields has to be
    // captured for this client — a subscription that only takes effect at the *end* of its own
    // backfill silently drops them. That is exactly what happened to a short run's final event and
    // terminal lifecycle while a long run in the same lane was still being sent: the client was
    // left showing a finished run as running, with the event missing altogether.
    for (const id of laneIds) state.lanes.add(id);
    state.backfilling += 1;
    try {
      for (const laneId of laneIds) {
        if (!repo.getLane(this.deps.db, laneId)) continue;
        this.send(state.ws, {
          kind: 'lane-state',
          laneId,
          runs: repo.listRuns(this.deps.db, laneId).map(repo.lifecycleOf),
        });
      }
      for (const [runId, lastSeq] of Object.entries(resume)) {
        const run = repo.getRun(this.deps.db, runId);
        if (!run || !wanted.has(run.laneId)) continue;
        const cursor = await this.backfill(state, run.laneId, runId, lastSeq);
        if (cursor === null) return; // the client went away mid-history
        // An event queued while the history was going out is already in the backfill if it was
        // persisted before the last page read. Dropping those here keeps each run's sequence
        // strictly increasing rather than relying on the client's reducer to ignore duplicates.
        state.pending = state.pending.filter(
          (item) => item.kind !== 'event' || item.env.runId !== runId || item.env.seq > cursor,
        );
      }
    } finally {
      state.backfilling -= 1;
    }
    this.flushClient(state);
    this.send(state.ws, { kind: 'subscribed', laneIds });
  }

  /**
   * Send one run's stored events from `afterSeq`, a page at a time, waiting for the socket between
   * pages. Answers the sequence actually reached, or `null` if the client went away.
   *
   * Reading a whole trajectory into memory before sending any of it would make a reconnect's cost
   * the size of the run. Sending it all without yielding is worse: the socket cannot drain while
   * this loop holds the thread, so its buffer grows past the cap purely because of our own writes,
   * and the hub used to disconnect a healthy browser half way through its own history.
   */
  private async backfill(
    state: ClientState,
    laneId: string,
    runId: string,
    afterSeq: number,
  ): Promise<number | null> {
    let cursor = afterSeq;
    for (;;) {
      const rows = repo.listEvents(this.deps.db, runId, cursor, BACKFILL_PAGE);
      if (rows.length === 0) return cursor;
      const envelopes: Envelope[] = [];
      for (const row of rows) {
        try {
          envelopes.push(repo.toEnvelope(row, laneId));
        } catch (err) {
          console.warn('hub: skipping corrupt event row', runId, row.seq, err);
        }
        cursor = row.seq;
      }
      if (envelopes.length > 0) this.send(state.ws, { kind: 'events', envelopes });
      if (!(await this.drain(state))) return null;
      if (rows.length < BACKFILL_PAGE) return cursor;
    }
  }

  private unsubscribe(state: ClientState, laneIds: string[]): void {
    for (const id of laneIds) state.lanes.delete(id);
    this.send(state.ws, { kind: 'unsubscribed', laneIds });
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      for (const c of this.clients) this.flushClient(c);
    }, this.flushMs);
  }

  /**
   * Hand this client as much of its queue as its socket will take, in order.
   *
   * Whatever is left stays queued and a further flush is scheduled, so buffered work has an owner
   * even if nothing is published again: a run's last event and its terminal lifecycle must not sit
   * in the queue for ever merely because they were the last things to happen.
   */
  private flushClient(c: ClientState): void {
    // While a backfill is running these items belong after the history it is still sending. They
    // stay queued (bounded by `MAX_PENDING`); the backfill flushes them when it finishes.
    if (c.backfilling > 0 || c.pending.length === 0) return;
    while (c.pending.length > 0) {
      // Checked between chunks, not once at the start: a large queue can fill the socket part-way
      // through, and the rest of it is better left here than handed to a socket that cannot take it.
      if (this.overBuffered(c)) {
        c.stalledSince ??= Date.now();
        // A socket that is merely busy comes back under the mark within a flush or two. One that
        // stays over it for `stallMs` is a connection that has stopped reading.
        if (Date.now() - c.stalledSince > this.stallMs)
          this.dropSlowClient(c, `a socket buffer that has not drained in ${this.stallMs} ms`);
        else this.scheduleFlush();
        return;
      }
      c.stalledSince = null;
      const head = c.pending[0] as Outbound;
      if (head.kind === 'run') {
        c.pending.shift();
        this.send(c.ws, { kind: 'run', lifecycle: head.lifecycle });
        continue;
      }
      // as many consecutive events as fit in one frame, so a long stream is not one frame per event
      const envelopes: Envelope[] = [];
      while (envelopes.length < 500) {
        const next = c.pending[0];
        if (next?.kind !== 'event') break;
        c.pending.shift();
        envelopes.push(next.env);
      }
      this.send(c.ws, { kind: 'events', envelopes });
    }
  }

  private send(ws: WebSocket, msg: WsServerMessage): void {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }
}
