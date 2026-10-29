import { describe, expect, it } from 'vitest';
import { deriveBrowserView } from '@/core/browser';
import { applyEnvelopes, createRunView } from '@/core/reducer';
import type { BrowserMode, Envelope, RunLifecycle } from '@/core/types';
import { openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { Replayer } from '@/server/replay';
import { loadFixture, readFixtureLines } from '../helpers/fixtures';

class FakeHub {
  events: Envelope[] = [];
  lifecycles: RunLifecycle[] = [];
  publishEvent(e: Envelope) {
    this.events.push(e);
  }
  publishLifecycle(l: RunLifecycle) {
    this.lifecycles.push(l);
  }
}
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function seeded() {
  return seededWithHandle().db;
}

/** As `seeded`, plus the raw connection — for the tests that inject a database-level write failure. */
function seededWithHandle() {
  const { db, sqlite } = openDb(':memory:');
  repo.createLane(db, { id: 'lane', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
  repo.createRun(db, {
    id: 'src',
    laneId: 'lane',
    prompt: 'fix it',
    effectiveCwd: '/tmp',
    permission: 'allowlist',
    origin: 'execution',
    status: 'finished',
    startedAt: 1,
    endedAt: 2,
  });
  repo.updateRun(db, 'src', {
    costUsd: 0.77,
    durationMs: 81598,
    numTurns: 12,
    sessionId: '066df07f-cf42-4d5a-b156-e83d817ad021',
  });
  for (const env of loadFixture('flat', { laneId: 'lane', runId: 'src' })) {
    repo.insertEvent(db, {
      runId: 'src',
      seq: env.seq,
      receivedAt: env.receivedAt,
      type: env.event.type,
      parentToolUseId: env.event.parent_tool_use_id ?? null,
      json: JSON.stringify(env.event),
    });
  }
  return { db, sqlite };
}

/**
 * A three-event source recording: events 0 ms / 250 ms / 1000 ms into a 1 s run. Pass `endedAt: null`
 * for a source that is still running (or whose run row predates its own events) — `undefined` would
 * only re-trigger the default parameter, and the spread in `repo.createRun` would drop it anyway.
 */
function seededTiming(endedAt: number | null = 2000) {
  return seededTimingWithHandle(endedAt).db;
}

/** As `seededTiming`, plus the raw connection — for the tests that inject a write failure. */
function seededTimingWithHandle(endedAt: number | null = 2000) {
  const { db, sqlite } = openDb(':memory:');
  repo.createLane(db, { id: 'lane', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
  repo.createRun(db, {
    id: 'src',
    laneId: 'lane',
    prompt: 'time me',
    effectiveCwd: '/tmp',
    permission: 'allowlist',
    origin: 'execution',
    status: 'finished',
    startedAt: 1000,
    endedAt,
  });
  const rows = [
    { receivedAt: 1000, event: { type: 'system', subtype: 'init', session_id: 's1', cwd: '/tmp' } },
    { receivedAt: 1250, event: { type: 'assistant', message: { id: 'm1', model: 'test', content: [] } } },
    {
      receivedAt: 2000,
      event: {
        type: 'result',
        subtype: 'success',
        duration_ms: 1000,
        num_turns: 1,
        total_cost_usd: 0.01,
        session_id: 's1',
      },
    },
  ];
  rows.forEach((row, i) => {
    repo.insertEvent(db, {
      runId: 'src',
      seq: i + 1,
      receivedAt: row.receivedAt,
      type: row.event.type,
      parentToolUseId: null,
      json: JSON.stringify(row.event),
    });
  });
  return { db, sqlite };
}

describe('Replayer', () => {
  it('replays instantly as a new run with fresh seqs and the source outcome', () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 5000 });
    const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
    expect(runId).not.toBe('src');
    expect(hub.events.filter((e) => e.runId === runId)).toHaveLength(73);
    expect(hub.events[0]?.seq).toBe(1);
    expect(repo.listEvents(db, runId)).toHaveLength(73);
    const lc = hub.lifecycles.filter((l) => l.runId === runId);
    expect(lc[0]?.status).toBe('running');
    expect(lc.at(-1)).toMatchObject({
      status: 'finished',
      sessionId: '066df07f-cf42-4d5a-b156-e83d817ad021',
    });
    expect(repo.getRun(db, runId)).toMatchObject({
      replayOf: 'src',
      prompt: 'fix it',
      status: 'finished',
      costUsd: 0.77,
    });
  });

  /**
   * A6. A playback must say what the recorded run asked for, including when it did not get it.
   *
   * The replay row copied prompt, directory, permission and model but not `browser`, so
   * `createRun`'s `off` default took over. Browser evidence is derived from the *request* together
   * with the events (`deriveBrowserView`): a Chrome run whose `init` carried no browser tools is
   * `unavailable` — a configuration failure worth seeing — and the same events under `off` derive
   * `off`, which reads as a session that never wanted a browser. Replaying the run therefore hid
   * the very thing it recorded.
   *
   * Both directions are covered: the recorded failure stays visible, and a recording made with the
   * browser off stays off however the console's later defaults change.
   */
  describe('carries the recording’s browser setting', () => {
    /** A run with one `init` event, and no browser tools in it. */
    function withInit(browser: BrowserMode, runId: string) {
      const { db } = openDb(':memory:');
      repo.createLane(db, { id: 'lane', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
      repo.createRun(db, {
        id: runId,
        laneId: 'lane',
        prompt: 'research it',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        browser,
        origin: 'execution',
        status: 'finished',
        startedAt: 1,
        endedAt: 2,
      });
      const init = JSON.stringify({
        type: 'system',
        subtype: 'init',
        session_id: 's',
        tools: ['Read', 'Bash'],
        mcp_servers: [],
      });
      repo.insertEvent(db, {
        runId,
        seq: 1,
        receivedAt: 1,
        type: 'system',
        parentToolUseId: null,
        json: init,
      });
      return db;
    }

    /** What the interface would show for this run: the row's setting folded over its events. */
    function statusOf(db: ReturnType<typeof openDb>['db'], runId: string) {
      const row = repo.getRun(db, runId) as NonNullable<ReturnType<typeof repo.getRun>>;
      const view = applyEnvelopes(
        createRunView({
          runId,
          laneId: row.laneId,
          prompt: row.prompt,
          cwd: row.effectiveCwd,
          startedAt: row.startedAt,
          browser: row.browser,
        }),
        repo.listEvents(db, runId).map((e, i) => ({
          laneId: row.laneId,
          runId,
          seq: i + 1,
          receivedAt: e.receivedAt,
          event: repo.eventOf(e),
        })),
      );
      return deriveBrowserView(view).status;
    }

    it('keeps a requested-but-unavailable browser visible in the playback', () => {
      const db = withInit('chrome', 'src');
      expect(statusOf(db, 'src')).toBe('unavailable');
      const rp = new Replayer({ db, hub: new FakeHub(), now: () => 5000 });
      const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
      expect(repo.getRun(db, runId)?.browser).toBe('chrome');
      expect(statusOf(db, runId)).toBe('unavailable');
    });

    it('keeps a recording made with the browser off truthful', () => {
      const db = withInit('off', 'src');
      const rp = new Replayer({ db, hub: new FakeHub(), now: () => 5000 });
      const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
      expect(repo.getRun(db, runId)?.browser).toBe('off');
      expect(statusOf(db, runId)).toBe('off');
    });
  });

  it('paces 4x replay by recorded gaps and can be cancelled', async () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub });
    const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
    await wait(50);
    const early = hub.events.filter((e) => e.runId === runId).length;
    expect(early).toBeGreaterThan(0);
    expect(early).toBeLessThan(73); // the real run took 80 s, so at 4x it is still going after 50 ms
    expect(rp.isLive(runId)).toBe(true);
    expect(rp.cancel(runId)).toBe(true);
    expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('cancelled');
    expect(rp.isLive(runId)).toBe(false);
    await wait(30);
    expect(hub.events.filter((e) => e.runId === runId).length).toBe(early);
  });

  it("stamps replayed envelopes with the recording's relative timing from the new run's start", () => {
    const db = seededTiming();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 50_000 });
    const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
    // the recording's 0 / 250 / 1000 ms offsets, re-based on the new run's startedAt
    expect(repo.listEvents(db, runId).map((e) => e.receivedAt)).toEqual([50_000, 50_250, 51_000]);
    expect(hub.events.filter((e) => e.runId === runId).map((e) => e.receivedAt)).toEqual([
      50_000, 50_250, 51_000,
    ]);
    const run = repo.getRun(db, runId);
    expect(run?.startedAt).toBe(50_000);
    expect(run?.endedAt).toBe(51_000); // the recording lasted 1 s, so the replay reports 1 s
  });

  it("stamps a 4x replay with the recording's relative timing too", async () => {
    const db = seededTiming();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 50_000 });
    const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
    // 250 ms + 750 ms of recorded gaps played at 4x is ~250 ms of wall time
    for (let i = 0; i < 100 && rp.isLive(runId); i += 1) await wait(20);
    expect(rp.isLive(runId)).toBe(false);
    // pacing is real time and divided by the speed factor; the stamps are not
    expect(repo.listEvents(db, runId).map((e) => e.receivedAt)).toEqual([50_000, 50_250, 51_000]);
    const run = repo.getRun(db, runId);
    expect(run?.startedAt).toBe(50_000);
    expect(run?.endedAt).toBe(51_000);
  });

  /**
   * A playback stopped part-way must not claim to have ended before the events it already sent.
   *
   * Its stamps are the recording's, re-based on this run's start, so at `4x` they run ahead of the
   * wall clock. Ending at `now` put the run's own bars outside its own axis — the run "ended" at
   * 50,000 while an event stamped 50,250 was already on the client.
   */
  it('ends a cancelled playback no earlier than the events it already sent', async () => {
    const db = seededTiming();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 50_000 });
    const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
    for (let i = 0; i < 100 && repo.listEvents(db, runId).length < 2; i += 1) await wait(10);
    const emitted = repo.listEvents(db, runId).map((e) => e.receivedAt);
    expect(emitted).toEqual([50_000, 50_250]);

    expect(rp.cancel(runId)).toBe(true);

    const run = repo.getRun(db, runId);
    expect(run?.status).toBe('cancelled');
    // covers what was emitted, and invents no further recording progress (the third event is at
    // 51,000 and never happened)
    expect(run?.endedAt).toBe(50_250);
    expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)).toMatchObject({
      status: 'cancelled',
      endedAt: 50_250,
    });
  });

  /**
   * A paced playback's writes happen inside `setTimeout`, where there is no caller to reject to: a
   * failed insert escaped as an uncaught exception, which takes down the server and every live
   * agent with it, and left the playback's row `running` for ever.
   */
  it('ends a playback that cannot be written down, instead of throwing out of a timer', async () => {
    const { db, sqlite } = seededWithHandle();
    const hub = new FakeHub();
    const uncaught: unknown[] = [];
    const onUncaught = (err: unknown) => uncaught.push(err);
    process.on('uncaughtException', onUncaught);
    try {
      const rp = new Replayer({ db, hub, now: () => 9000 });
      const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
      // the next event's insert is rejected by the database, part-way through the playback
      sqlite.exec(
        "CREATE TRIGGER reject_replay BEFORE INSERT ON events WHEN NEW.seq > 2 BEGIN SELECT RAISE(FAIL, 'disk full'); END;",
      );
      for (let i = 0; i < 200 && rp.isLive(runId); i += 1) await wait(10);

      expect(rp.isLive(runId)).toBe(false);
      expect(repo.getRun(db, runId)).toMatchObject({
        status: 'failed',
        errorMessage: expect.stringContaining('disk full'),
      });
      expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('failed');
      // the timer chain is stopped, not merely interrupted once
      const emitted = hub.events.filter((e) => e.runId === runId).length;
      await wait(60);
      expect(hub.events.filter((e) => e.runId === runId).length).toBe(emitted);
      expect(uncaught).toEqual([]);
    } finally {
      process.off('uncaughtException', onUncaught);
    }
  });

  /**
   * The same mechanism as above, one step later: the events all landed and it is the *terminal
   * update* that is rejected. The old code had already released ownership before attempting that
   * write, so the rejection escaped the timer as an uncaught exception and left a `running` row
   * with nothing live to finish it.
   */
  it('ends failed when a playback outcome cannot be written, rather than throwing out of a timer', async () => {
    const { db, sqlite } = seededTimingWithHandle();
    const hub = new FakeHub();
    const uncaught: unknown[] = [];
    const onUncaught = (err: unknown) => uncaught.push(err);
    process.on('uncaughtException', onUncaught);
    try {
      // every attempt to mark *this* replay finished is rejected; ending it failed is still allowed
      sqlite.exec(
        "CREATE TRIGGER reject_finish BEFORE UPDATE OF status ON runs WHEN OLD.origin = 'replay' AND NEW.status = 'finished' BEGIN SELECT RAISE(FAIL, 'disk full'); END;",
      );
      const rp = new Replayer({ db, hub, now: () => 9000 });
      const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
      for (let i = 0; i < 400 && rp.isLive(runId); i += 1) await wait(10);

      expect(uncaught).toEqual([]);
      expect(rp.isLive(runId)).toBe(false);
      // a terminal state, reached the only way that was left
      expect(repo.getRun(db, runId)).toMatchObject({
        status: 'failed',
        errorMessage: 'the playback finished but its outcome could not be recorded',
      });
      expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('failed');
    } finally {
      process.off('uncaughtException', onUncaught);
    }
  }, 20_000);

  /**
   * An instant playback is not a special case for ownership. It used to be registered nowhere, so a
   * write that failed part-way threw out of `start` and left a persisted `running` replay that
   * nothing could finish, cancel or even see as live.
   */
  it('ends an instant playback that cannot be written down, instead of leaving an orphan', () => {
    const { db, sqlite } = seededWithHandle();
    const hub = new FakeHub();
    sqlite.exec(
      "CREATE TRIGGER reject_second BEFORE INSERT ON events WHEN NEW.run_id <> 'src' AND NEW.seq = 2 BEGIN SELECT RAISE(FAIL, 'disk full'); END;",
    );
    const rp = new Replayer({ db, hub, now: () => 9000 });

    const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });

    expect(rp.isLive(runId)).toBe(false);
    expect(repo.getRun(db, runId)).toMatchObject({
      status: 'failed',
      errorMessage: expect.stringContaining('disk full'),
    });
    // nothing is left claiming to be running in this lane
    expect(repo.listRuns(db, 'lane').filter((r) => r.status === 'running')).toEqual([]);
    expect(hub.lifecycles.filter((l) => l.runId === runId).map((l) => l.status)).toEqual([
      'running',
      'failed',
    ]);
    // the events that did land are kept as they were received
    expect(repo.listEvents(db, runId)).toHaveLength(1);
  });

  it('ends a replay at its last anchored event when the source has no endedAt', () => {
    const db = seededTiming(null); // e.g. replaying a run that is still going
    // guard: the fallback branch is only exercised while the source row really has no endedAt
    expect(repo.getRun(db, 'src')?.endedAt).toBeNull();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 50_000 });
    const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
    const run = repo.getRun(db, runId);
    expect(run?.startedAt).toBe(50_000);
    expect(run?.endedAt).toBe(51_000); // the last stamp, not the wall clock (which would give 50_000)
  });

  it('never writes a non-terminal status onto a replay', () => {
    const db = seeded();
    // the source is still going: copying its `running` onto the replay would leave a run that
    // nothing can ever finish or cancel (`replay.start` refuses this case; this is the backstop)
    repo.updateRun(db, 'src', { status: 'running', endedAt: null, errorMessage: null });
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 5000 });
    const runId = rp.start({ sourceRunId: 'src', speed: 'instant' });
    expect(repo.getRun(db, runId)).toMatchObject({
      status: 'failed',
      errorMessage: 'source run had not finished',
    });
    expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('failed');
  });

  it('imports raw JSONL lines into a lane and derives the outcome', () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 9000 });
    const runId = rp.import({ laneId: 'lane', lines: readFixtureLines('failed'), label: 'failed.jsonl' });
    expect(repo.listEvents(db, runId)).toHaveLength(17);
    const run = repo.getRun(db, runId);
    expect(run).toMatchObject({ status: 'failed', numTurns: 3, prompt: 'Imported: failed.jsonl' });
    expect(run?.errorMessage).toContain('Reached maximum number of turns');
    expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('failed');
  });

  it("imports with the recording's relative timing anchored at import time", () => {
    const db = seeded();
    const hub = new FakeHub();
    // the recording is older than this wall clock, which is the case the anchoring exists for: its
    // 1 s gaps have to survive the import instead of collapsing onto `now`
    const now = Date.parse('2026-12-01T00:00:00Z');
    const rp = new Replayer({ db, hub, now: () => now });
    const lines = [
      '{"type":"system","subtype":"init","session_id":"s1","cwd":"/tmp","timestamp":"2026-09-16T10:00:00Z"}',
      '{"type":"assistant","message":{"id":"m1","model":"test","content":[]},"timestamp":"2026-09-16T10:00:01Z"}',
      '{"type":"result","subtype":"success","duration_ms":2000,"num_turns":1,"total_cost_usd":0.01,"session_id":"s1","timestamp":"2026-09-16T10:00:02Z"}',
    ];
    const runId = rp.import({ laneId: 'lane', lines, label: 'recorded.jsonl' });
    expect(repo.listEvents(db, runId).map((e) => e.receivedAt)).toEqual([now, now + 1000, now + 2000]);
    const run = repo.getRun(db, runId);
    expect(run?.startedAt).toBe(now); // anchored at import time, so the run is never back-dated
    expect(run?.endedAt).toBe(now + 2000); // ... but the recording's 2 s duration survives
    expect(hub.lifecycles.filter((l) => l.runId === runId)[0]?.startedAt).toBe(now);
  });

  it('ends an import at its last anchored event when the recording has no result', () => {
    const db = seeded();
    const hub = new FakeHub();
    const now = Date.parse('2026-12-01T00:00:00Z');
    const rp = new Replayer({ db, hub, now: () => now });
    const lines = [
      '{"type":"system","subtype":"init","session_id":"s1","cwd":"/tmp","timestamp":"2026-09-16T10:00:00Z"}',
      '{"type":"assistant","message":{"id":"m1","model":"test","content":[]},"timestamp":"2026-09-16T10:00:03Z"}',
    ];
    const runId = rp.import({ laneId: 'lane', lines, label: 'truncated.jsonl' });
    const run = repo.getRun(db, runId);
    expect(run?.status).toBe('failed');
    expect(run?.errorMessage).toBe('imported stream has no result event');
    // the run still has a real wall time, so its bars stay inside the compare timeline's axis
    expect(run?.endedAt).toBe(now + 3000);
  });

  /**
   * An import is written in one transaction. Before that, the run row was created `running`, the
   * events were appended one at a time, and the status was patched in at the end — so a write that
   * failed part-way left a row stuck at `running` in a lane nothing would ever finish, holding that
   * lane's one-operation slot, with half a recording under it.
   */
  it('leaves nothing behind when a recording cannot be written in full', () => {
    const { db, sqlite } = seededWithHandle();
    const hub = new FakeHub();
    // a real mid-write failure: the third event's insert is rejected by the database
    sqlite.exec(
      "CREATE TRIGGER reject_third BEFORE INSERT ON events WHEN NEW.seq = 3 BEGIN SELECT RAISE(FAIL, 'disk full'); END;",
    );
    const rp = new Replayer({ db, hub, now: () => 9000 });
    const before = repo.listRuns(db, 'lane').map((r) => r.id);
    expect(() =>
      rp.import({ laneId: 'lane', lines: readFixtureLines('failed'), label: 'failed.jsonl' }),
    ).toThrow(/disk full/);
    // no orphan run, no partial recording, and nothing announced to any client
    expect(repo.listRuns(db, 'lane').map((r) => r.id)).toEqual(before);
    expect(hub.lifecycles).toHaveLength(0);
    expect(hub.events).toHaveLength(0);

    // and with the failure removed the same import lands whole
    sqlite.exec('DROP TRIGGER reject_third');
    const runId = rp.import({ laneId: 'lane', lines: readFixtureLines('failed'), label: 'failed.jsonl' });
    expect(repo.listEvents(db, runId)).toHaveLength(17);
    expect(repo.getRun(db, runId)?.status).toBe('failed');
  });

  it('announces an import only once, in the state it is already in', () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 9000 });
    const runId = rp.import({ laneId: 'lane', lines: readFixtureLines('flat'), label: 'flat.jsonl' });
    const lifecycles = hub.lifecycles.filter((l) => l.runId === runId);
    // No `running` first: an import is instantaneous, and a run announced as running is one the
    // lane guard and the composer's Stop both have to treat as live.
    expect(lifecycles.map((l) => l.status)).toEqual(['finished']);
    expect(lifecycles[0]?.origin).toBe('import');
    // the events still reach the client, after the row they belong to
    expect(hub.events.filter((e) => e.runId === runId)).toHaveLength(73);
  });

  it('ends paced playbacks on shutdown, while the database is still open', async () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub });
    const runId = rp.start({ sourceRunId: 'src', speed: '4x' });
    expect(rp.isLive(runId)).toBe(true);
    const emitted = hub.events.filter((e) => e.runId === runId).length;

    rp.shutdown();

    expect(rp.isLive(runId)).toBe(false);
    expect(repo.getRun(db, runId)?.status).toBe('cancelled');
    expect(hub.lifecycles.filter((l) => l.runId === runId).at(-1)?.status).toBe('cancelled');
    // the timer chain really is stopped: nothing more is written after the connection would close
    await wait(60);
    expect(hub.events.filter((e) => e.runId === runId).length).toBe(emitted);
    // shutting down twice is a no-op, not a second cancellation
    rp.shutdown();
    expect(hub.lifecycles.filter((l) => l.runId === runId && l.status === 'cancelled')).toHaveLength(1);
  });

  it('keeps imported receivedAt non-decreasing across a backward timestamp jump', () => {
    const db = seeded();
    const hub = new FakeHub();
    const rp = new Replayer({ db, hub, now: () => 9000 });
    const lines = [
      '{"type":"system","subtype":"init","session_id":"s1","cwd":"/tmp","timestamp":"2026-01-01T00:00:00.010Z"}',
      '{"type":"assistant","message":{"id":"m1","model":"test","content":[]},"timestamp":"2026-01-01T00:00:00.007Z"}',
      '{"type":"result","subtype":"success","duration_ms":50,"num_turns":1,"total_cost_usd":0.01,"session_id":"s1","timestamp":"2026-01-01T00:00:00.020Z"}',
    ];
    const runId = rp.import({ laneId: 'lane', lines });
    const receivedAts = repo.listEvents(db, runId).map((e) => e.receivedAt);
    expect(receivedAts).toHaveLength(3);
    for (let i = 1; i < receivedAts.length; i += 1) {
      expect(receivedAts[i]).toBeGreaterThanOrEqual(receivedAts[i - 1] as number);
    }
  });
});
