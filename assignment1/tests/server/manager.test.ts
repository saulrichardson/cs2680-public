import { spawn } from 'node:child_process';
import { chmodSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Envelope, RunLifecycle } from '@/core/types';
import { type Db, openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { outcomeStatus, ProcessManager } from '@/server/process/manager';
import { makeTmpDir } from '../helpers/tmp';

const FAKE = fileURLToPath(new URL('../fake-claude/claude', import.meta.url));

/**
 * A one-off stand-in agent for cases the shared fake CLI does not cover: writing to stderr, and
 * staying alive *after* emitting its result so a Stop can land behind it.
 */
function writeAgent(name: string, body: string): string {
  const path = join(tmp.path, name);
  writeFileSync(path, `#!/usr/bin/env node\n${body}\n`);
  chmodSync(path, 0o755);
  return path;
}

const RESULT_LINE = JSON.stringify({
  type: 'result',
  subtype: 'success',
  is_error: false,
  duration_ms: 12,
  duration_api_ms: 10,
  num_turns: 2,
  total_cost_usd: 0.01,
  session_id: 'late-stop-session',
  usage: {},
  modelUsage: {},
});
const INIT_LINE = JSON.stringify({ type: 'system', subtype: 'init', session_id: 'late-stop-session' });

/**
 * A tool the agent starts that refuses every polite signal, as a `node -e` program. It records its
 * own pid *after* installing the handlers, so a test that waits for the file knows the process is
 * genuinely stubborn rather than still booting — a pid written by its parent proves nothing, and a
 * signal that lands before the handlers are installed kills it by default.
 */
const STUBBORN_TOOL = [
  "process.on('SIGINT', () => {});",
  "process.on('SIGTERM', () => {});",
  "require('node:fs').writeFileSync(process.argv[1], String(process.pid));",
  'setInterval(() => {}, 1000);',
].join('');

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

function waitFor(pred: () => boolean, ms = 10_000): Promise<void> {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      if (pred()) resolve();
      else if (Date.now() - t0 > ms) reject(new Error('timeout'));
      else setTimeout(tick, 10);
    };
    tick();
  });
}

let db: Db;
let sqlite: Database.Database;
let hub: FakeHub;
let pm: ProcessManager;
const tmp = makeTmpDir();

/** Is this OS process still there? Signal 0 asks without sending anything. */
function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
const terminal = (runId: string) =>
  hub.lifecycles.some((l) => l.runId === runId && ['finished', 'failed', 'cancelled'].includes(l.status));
const last = (runId: string) => hub.lifecycles.filter((l) => l.runId === runId).at(-1) as RunLifecycle;

beforeEach(() => {
  ({ db, sqlite } = openDb(':memory:'));
  repo.createLane(db, { id: 'lane', name: 'L', cwd: tmp.path, permission: 'allowlist', createdAt: 1 });
  hub = new FakeHub();
  pm = new ProcessManager({ db, hub, claudeBin: FAKE, cancelGraceMs: 200 });
});
afterEach(async () => {
  await pm.shutdown();
});

describe('ProcessManager', () => {
  it('runs the flat fixture to finished, persisting every event in order', async () => {
    // `FIXTURE:flat` pins the recording: the numbers below are flat.jsonl's, and the fake CLI
    // otherwise picks a fixture from the prompt's wording.
    await pm.start({
      runId: 'r1',
      laneId: 'lane',
      prompt: 'FIXTURE:flat fix it',
      cwd: tmp.path,
      permission: 'allowlist',
    });
    expect(hub.lifecycles[0]).toMatchObject({ runId: 'r1', status: 'running' });
    await waitFor(() => terminal('r1'));
    const lc = last('r1');
    expect(lc.status).toBe('finished');
    expect(lc.numbers).toEqual({
      costUsd: 0.7716919999999999,
      durationMs: 81598,
      durationApiMs: 77768,
      numTurns: 12,
    });
    expect(lc.sessionId).toBe('066df07f-cf42-4d5a-b156-e83d817ad021');
    const stored = repo.listEvents(db, 'r1');
    expect(stored).toHaveLength(73);
    expect(stored.map((e) => e.seq)).toEqual(Array.from({ length: 73 }, (_, i) => i + 1));
    expect(hub.events.filter((e) => e.runId === 'r1')).toHaveLength(73);
    expect(repo.getRun(db, 'r1')).toMatchObject({
      status: 'finished',
      exitCode: 0,
      model: 'claude-opus-5[1m]',
    });
  });

  it('fails immediately for a directory that does not exist, without spawning', async () => {
    await pm.start({
      runId: 'r2',
      laneId: 'lane',
      prompt: 'x',
      cwd: '/definitely/not/here',
      permission: 'allowlist',
    });
    const lc = last('r2');
    expect(lc.status).toBe('failed');
    expect(lc.error?.message).toContain('is not a directory');
    expect(pm.isLive('r2')).toBe(false);
    expect(repo.listEvents(db, 'r2')).toHaveLength(0);
  });

  it('fails when the CLI exits non-zero without a result', async () => {
    await pm.start({
      runId: 'r3',
      laneId: 'lane',
      prompt: 'please FAIL_EXIT now',
      cwd: tmp.path,
      permission: 'allowlist',
    });
    await waitFor(() => terminal('r3'));
    const lc = last('r3');
    expect(lc.status).toBe('failed');
    expect(lc.error).toMatchObject({ exitCode: 3 });
    expect(lc.error?.message).toMatch(/exited with code 3/);
  });

  it('fails when the CLI exits zero without a result event', async () => {
    await pm.start({
      runId: 'r4',
      laneId: 'lane',
      prompt: 'NO_RESULT',
      cwd: tmp.path,
      permission: 'allowlist',
    });
    await waitFor(() => terminal('r4'));
    expect(last('r4').status).toBe('failed');
    expect(last('r4').error?.message).toMatch(/without a result/);
  });

  it('stores a malformed line verbatim as an unparsed event and keeps going', async () => {
    await pm.start({
      runId: 'r5',
      laneId: 'lane',
      prompt: 'GARBAGE',
      cwd: tmp.path,
      permission: 'allowlist',
    });
    await waitFor(() => terminal('r5'));
    expect(last('r5').status).toBe('finished');
    const unparsed = repo.listEvents(db, 'r5').filter((e) => e.type === 'unparsed');
    expect(unparsed).toHaveLength(1);
    // the column holds the agent's original text (what `/api/export` hands back), not a
    // re-serialized envelope, and the `unparsed` shape is re-derived on read
    const row = unparsed[0] as (typeof unparsed)[number];
    expect(row.json).toBe('{this is not json');
    expect(repo.eventOf(row)).toMatchObject({ type: 'unparsed', raw: '{this is not json' });
    // and the live broadcast carried that same shape
    expect(hub.events.find((e) => e.runId === 'r5' && e.event.type === 'unparsed')?.event).toMatchObject({
      raw: '{this is not json',
    });
  });

  it('cancels with SIGINT first and records cancelled', async () => {
    await pm.start({ runId: 'r6', laneId: 'lane', prompt: 'HANG', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r6'));
    expect(pm.isLive('r6')).toBe(true);
    expect(await pm.cancel('r6')).toBe(true);
    await waitFor(() => terminal('r6'));
    // The Stop came first, so the run is cancelled whatever the interrupted turn writes on its way
    // out (the fake replays the real CLI's `error_during_execution` answer to SIGINT).
    expect(last('r6').status).toBe('cancelled');
    expect(pm.isLive('r6')).toBe(false);
    expect(await pm.cancel('r6')).toBe(false);
  });

  it('reports finished when the Stop arrives after the result, and keeps stderr for a cancelled run', async () => {
    const lateStop = writeAgent(
      'late-stop-agent.mjs',
      `process.stdout.write(${JSON.stringify(`${INIT_LINE}\n${RESULT_LINE}\n`)});
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: lateStop, cancelGraceMs: 200 });
    try {
      await pm2.start({ runId: 'r10', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.filter((e) => e.runId === 'r10').length === 2);
      expect(await pm2.cancel('r10')).toBe(true);
      await waitFor(() => terminal('r10'));
      expect(last('r10').status).toBe('finished');
      expect(last('r10').numbers?.numTurns).toBe(2);
      expect(repo.getRun(db, 'r10')?.status).toBe('finished');
    } finally {
      await pm2.shutdown();
    }

    // A run stopped before any result stays cancelled, and its stderr tail still reaches the row so
    // the inspector can show the agent's last words even with no error message of its own.
    const noisy = writeAgent(
      'noisy-agent.mjs',
      `process.stderr.write('a warning from the agent\\n');
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm3 = new ProcessManager({ db, hub, claudeBin: noisy, cancelGraceMs: 200 });
    try {
      await pm3.start({ runId: 'r11', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.some((e) => e.runId === 'r11'));
      expect(await pm3.cancel('r11')).toBe(true);
      await waitFor(() => terminal('r11'));
      expect(last('r11').status).toBe('cancelled');
      expect(repo.getRun(db, 'r11')).toMatchObject({
        status: 'cancelled',
        errorMessage: null,
        stderrTail: 'a warning from the agent\n',
      });
    } finally {
      await pm3.shutdown();
    }
  });

  /**
   * R4. A run is over when its process is, not when it says it is.
   *
   * The lane guard used to free up as soon as the CLI printed its `result`, on the reasoning that a
   * child which has reported will not touch the directory again. That is a claim about a process
   * nobody is controlling any more: it still holds the working directory, can still write, and is
   * still the thing a Stop would have to reach. The client does not need the early release either —
   * a run's status comes only from the server's lifecycle, which is published when the child exits,
   * so the composer's button and this guard change at the same moment.
   */
  it('keeps owning a run until its process has actually exited', async () => {
    const lingering = writeAgent(
      'lingering-agent.mjs',
      `process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setTimeout(() => process.stdout.write(${JSON.stringify(`${RESULT_LINE}\n`)}), 100);
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: lingering, cancelGraceMs: 200 });
    try {
      await pm2.start({ runId: 'r13', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.some((e) => e.runId === 'r13' && e.event.type === 'result'));
      // the result has landed, but the process has not gone anywhere
      expect(pm2.isLive('r13')).toBe(true);
      expect(repo.getRun(db, 'r13')?.status).toBe('running');
      expect(terminal('r13')).toBe(false);
      // ... and a Stop still reaches it
      expect(await pm2.cancel('r13')).toBe(true);
      await waitFor(() => terminal('r13'));
      expect(pm2.isLive('r13')).toBe(false);
      // it had already reported cleanly, so the Stop did not turn a finished run into a cancelled one
      expect(last('r13').status).toBe('finished');
    } finally {
      await pm2.shutdown();
    }
  });

  /**
   * R4, the reproduction. When an event cannot be persisted the run is over as far as the operator
   * is concerned — but the child is not. The manager used to write the failure, forget the run, and
   * send one SIGINT; a child that ignores SIGINT then kept running, holding the scratch directory,
   * with nothing left watching it. The outcome and the reaping are now separate: the outcome is
   * written once, and the process keeps being escalated until it is really gone.
   */
  it('reaps a child that ignores signals after a fatal persistence failure', async () => {
    const stubborn = writeAgent(
      'stubborn-agent.mjs',
      `process.on('SIGINT', () => {});
       process.on('SIGTERM', () => {});
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setInterval(() => {}, 1000);`,
    );
    // the cheapest real insert failure: the row for seq 1 is already there
    repo.insertEvent(db, {
      runId: 'r14',
      seq: 1,
      receivedAt: 1,
      type: 'system',
      parentToolUseId: null,
      json: '{"type":"system","subtype":"init"}',
    });
    const pm2 = new ProcessManager({ db, hub, claudeBin: stubborn, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r14', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => terminal('r14'));
      // the operator is told immediately ...
      expect(last('r14').status).toBe('failed');
      expect(last('r14').error?.message).toMatch(/^could not persist events: /);
      // ... while the manager keeps the run, and the escalation, until the child is gone
      expect(pm2.isLive('r14')).toBe(true);
      const pid = pm2.pidOf('r14');
      expect(pid).toBeGreaterThan(0);
      await waitFor(() => !alive(pid as number), 5000);
      await waitFor(() => !pm2.isLive('r14'));
      // the outcome written first is the one that stands: nothing overwrote it on exit
      expect(repo.getRun(db, 'r14')?.errorMessage).toMatch(/^could not persist events: /);
      expect(last('r14').status).toBe('failed');
      // and no further events were appended after the failure
      expect(repo.listEvents(db, 'r14').map((e) => e.seq)).toEqual([1]);
    } finally {
      await pm2.shutdown();
    }
  });

  it('kills the tools an agent started, not only the agent', async () => {
    // The CLI runs the operator's commands as its own children. A Stop that only signals the CLI
    // leaves those behind, still holding the directory the next run is about to use.
    const grandchildPid = join(tmp.path, 'grandchild.pid');
    const withChild = writeAgent(
      'spawner-agent.mjs',
      `import { spawn } from 'node:child_process';
       spawn(process.execPath, ['-e', ${JSON.stringify(STUBBORN_TOOL)}, ${JSON.stringify(grandchildPid)}], { stdio: 'ignore' });
       process.on('SIGINT', () => {});
       process.on('SIGTERM', () => {});
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: withChild, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r15', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => existsSync(grandchildPid) && hub.events.some((e) => e.runId === 'r15'));
      const kid = Number(readFileSync(grandchildPid, 'utf8'));
      expect(alive(kid)).toBe(true);

      expect(await pm2.cancel('r15')).toBe(true);
      await waitFor(() => terminal('r15'));
      await waitFor(() => !alive(kid), 5000);
      expect(alive(kid)).toBe(false);
    } finally {
      await pm2.shutdown();
    }
  });

  /**
   * The ownership defect the grandchild test above cannot see, because there the agent is stubborn
   * too and so stays alive for the whole escalation.
   *
   * A real CLI ends its turn on SIGINT — promptly. The tool it left running does not. The manager
   * used to forget the run the moment the *leader* exited, and its signal method refused to
   * escalate once the leader was gone, so the tool outlived the cancellation, the shutdown and the
   * server. What the manager owns is the process *group* it created, and the group outlives its
   * leader.
   */
  it('keeps reaping the group after the agent itself has exited', async () => {
    const toolPid = join(tmp.path, 'tool.pid');
    const politeAgent = writeAgent(
      'polite-agent.mjs',
      `import { spawn } from 'node:child_process';
       // A tool that ignores every polite signal, with no pipes back to us. It writes its own pid
       // only after its handlers are installed, so the file's existence proves it is really
       // stubborn — a pid written by the parent races the tool's own startup.
       spawn(process.execPath, ['-e', ${JSON.stringify(STUBBORN_TOOL)}, ${JSON.stringify(toolPid)}], { stdio: 'ignore' });
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       // ... and an agent that does what it is told, at once
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: politeAgent, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r20', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => existsSync(toolPid) && hub.events.some((e) => e.runId === 'r20'));
      const tool = Number(readFileSync(toolPid, 'utf8'));
      const leader = pm2.pidOf('r20') as number;
      expect(alive(tool)).toBe(true);

      expect(await pm2.cancel('r20')).toBe(true);
      // the agent goes immediately ...
      await waitFor(() => !alive(leader), 5000);
      // ... and the run is still owned, because what it started is still running
      expect(pm2.isLive('r20')).toBe(true);
      // ... until the escalation reaches the tool too
      await waitFor(() => !alive(tool), 5000);
      expect(alive(tool)).toBe(false);
      await waitFor(() => !pm2.isLive('r20'));
      expect(last('r20').status).toBe('cancelled');
    } finally {
      await pm2.shutdown();
    }
  });

  it('leaves nothing of a run behind at shutdown, including tools that outlive the agent', async () => {
    const toolPid = join(tmp.path, 'shutdown-tool.pid');
    const politeAgent = writeAgent(
      'polite-shutdown-agent.mjs',
      `import { spawn } from 'node:child_process';
       spawn(process.execPath, ['-e', ${JSON.stringify(STUBBORN_TOOL)}, ${JSON.stringify(toolPid)}], { stdio: 'ignore' });
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: politeAgent, cancelGraceMs: 150 });
    await pm2.start({ runId: 'r21', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => existsSync(toolPid) && hub.events.some((e) => e.runId === 'r21'));
    const tool = Number(readFileSync(toolPid, 'utf8'));

    await pm2.shutdown();

    expect(alive(tool)).toBe(false);
    expect(pm2.isLive('r21')).toBe(false);
  });

  /**
   * A run that ends *on its own* is a different case, and the manager deliberately does not reach
   * into it: if the operator asked the agent to start a dev server, that server is theirs. The run
   * is over when the agent is, and nothing the agent chose to leave running is killed.
   */
  it('leaves a background process alone when the agent finishes by itself', async () => {
    const backgroundPid = join(tmp.path, 'background.pid');
    const leaver = writeAgent(
      'leaver-agent.mjs',
      `import { spawn } from 'node:child_process';
       import { writeFileSync } from 'node:fs';
       const bg = spawn(process.execPath, ['-e', 'setTimeout(()=>{},4000)'], { stdio: 'ignore' });
       writeFileSync(${JSON.stringify(backgroundPid)}, String(bg.pid));
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n${RESULT_LINE}\n`)});
       process.exit(0);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: leaver, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r22', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => terminal('r22'));
      const background = Number(readFileSync(backgroundPid, 'utf8'));
      expect(last('r22').status).toBe('finished');
      // the run is over and the lane is free ...
      await waitFor(() => !pm2.isLive('r22'));
      // ... and what the agent deliberately left running is still running
      expect(alive(background)).toBe(true);
    } finally {
      await pm2.shutdown();
    }
  });

  /**
   * An init line that names neither a model nor a session is unusual, not broken. Writing "set
   * nothing" to the run row threw ("No values to set"), `onLine` read that as a fatal persistence
   * failure, and a perfectly healthy agent was reaped over a missing optional field — the run went
   * `failed` before anyone had even asked it to stop.
   */
  it('keeps running when an init event carries no model and no session id', async () => {
    const bare = writeAgent(
      'bare-init-agent.mjs',
      `process.stdout.write(${JSON.stringify(`${JSON.stringify({ type: 'system', subtype: 'init' })}\n`)});
       process.on('SIGINT', () => process.exit(0));
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: bare, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r25', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.some((e) => e.runId === 'r25'));
      await wait(100);

      // the line was stored, the run is still going, and nothing was reaped
      expect(repo.listEvents(db, 'r25')).toHaveLength(1);
      expect(repo.getRun(db, 'r25')).toMatchObject({ status: 'running', errorMessage: null });
      expect(pm2.isLive('r25')).toBe(true);
      expect(terminal('r25')).toBe(false);
      // the raw line is kept exactly as the agent wrote it
      expect(repo.listEvents(db, 'r25')[0]?.json).toBe('{"type":"system","subtype":"init"}');

      // ... and it still stops normally when asked
      expect(await pm2.cancel('r25')).toBe(true);
      await waitFor(() => terminal('r25'));
      expect(last('r25').status).toBe('cancelled');
    } finally {
      await pm2.shutdown();
    }
  });

  /**
   * When cleanup cannot be confirmed, the lane stays blocked.
   *
   * An earlier version released the run once its escalation deadline passed, logging that the
   * group "did not exit". That is the admission failure this model exists to prevent, arrived at
   * from the other side: a second agent let into a directory the first is still holding, at exactly
   * the moment we know least about what is in there. The honest state is "still occupied, and here
   * is why I could not clean it up".
   *
   * Exercised through the signalling seam rather than with a process that really cannot be killed:
   * the fake reports the group as alive while still killing it for real, so the test leaves nothing
   * behind.
   */
  it('keeps a lane blocked, with a reason, when it cannot confirm cleanup', async () => {
    let pretendAlive = true;
    const pm2 = new ProcessManager({
      db,
      hub,
      claudeBin: FAKE,
      cancelGraceMs: 20,
      control: {
        kill(pid, signal) {
          // signal 0 is the liveness question: answer "yes" for as long as the test wants
          if (signal === 0) {
            if (pretendAlive) return;
            throw Object.assign(new Error('no such process'), { code: 'ESRCH' });
          }
          // every real signal is delivered, so the child genuinely goes away
          process.kill(pid, signal);
        },
      },
    });
    try {
      await pm2.start({
        runId: 'r26',
        laneId: 'lane',
        prompt: 'HANG',
        cwd: tmp.path,
        permission: 'allowlist',
      });
      await waitFor(() => hub.events.some((e) => e.runId === 'r26'));
      const pid = pm2.pidOf('r26') as number;
      expect(await pm2.cancel('r26')).toBe(true);
      await waitFor(() => terminal('r26'));
      await waitFor(() => !alive(pid), 5000);

      // The escalation has run its course (20 ms grace), the process really is gone, but the
      // manager cannot tell — so the lane stays owned and says why.
      await waitFor(() => pm2.cleanupErrorOf('r26') !== undefined, 5000);
      expect(pm2.isLive('r26')).toBe(true);
      expect(pm2.cleanupErrorOf('r26')).toMatch(/could not confirm/);

      // ... and the moment it can tell, the lane frees itself without anyone asking again
      pretendAlive = false;
      await waitFor(() => !pm2.isLive('r26'), 5000);
      expect(pm2.cleanupErrorOf('r26')).toBeUndefined();
    } finally {
      pretendAlive = false;
      await pm2.shutdown();
    }
  });

  /**
   * A result that omits a figure has not reported it, and that is not the same as reporting zero.
   *
   * Both the reducer and this manager used to default an absent `total_cost_usd` to `0`, in the
   * view and in the row alike — so by the time anything aggregated them, an unreported cost and a
   * free run were the same value. In a race that is the difference between "we do not know what
   * this cost" and "this was the cheapest candidate".
   */
  it('keeps an unreported figure unreported, and a reported zero at zero', async () => {
    const partial = JSON.stringify({
      type: 'result',
      subtype: 'success',
      is_error: false,
      duration_ms: 100,
      num_turns: 1,
      session_id: 'partial-session',
    });
    const free = JSON.stringify({
      type: 'result',
      subtype: 'success',
      is_error: false,
      duration_ms: 50,
      num_turns: 1,
      total_cost_usd: 0,
      session_id: 'free-session',
    });
    const agent = (name: string, result: string) =>
      writeAgent(
        name,
        `process.stdout.write(${JSON.stringify(`${INIT_LINE}\n${result}\n`)});
         process.exit(0);`,
      );

    const pmPartial = new ProcessManager({ db, hub, claudeBin: agent('partial-agent.mjs', partial) });
    try {
      await pmPartial.start({
        runId: 'r27',
        laneId: 'lane',
        prompt: 'x',
        cwd: tmp.path,
        permission: 'allowlist',
      });
      await waitFor(() => terminal('r27'));
    } finally {
      await pmPartial.shutdown();
    }

    const pmFree = new ProcessManager({ db, hub, claudeBin: agent('free-agent.mjs', free) });
    try {
      await pmFree.start({
        runId: 'r28',
        laneId: 'lane',
        prompt: 'x',
        cwd: tmp.path,
        permission: 'allowlist',
      });
      await waitFor(() => terminal('r28'));
    } finally {
      await pmFree.shutdown();
    }

    // the row keeps the distinction: null for unreported, 0 for a real zero
    expect(repo.getRun(db, 'r27')).toMatchObject({ costUsd: null, durationMs: 100, numTurns: 1 });
    expect(repo.getRun(db, 'r28')).toMatchObject({ costUsd: 0, durationMs: 50, numTurns: 1 });
    // ... and so does what is published, and what a reload would rebuild from the row
    expect(last('r27').numbers).toMatchObject({ durationMs: 100, numTurns: 1 });
    expect(last('r27').numbers?.costUsd).toBeUndefined();
    expect(repo.lifecycleOf(repo.getRun(db, 'r27') as never).numbers?.costUsd).toBeUndefined();
    expect(repo.lifecycleOf(repo.getRun(db, 'r28') as never).numbers?.costUsd).toBe(0);
  });

  it('refuses to start a run once shutdown has begun', async () => {
    const pm2 = new ProcessManager({ db, hub, claudeBin: FAKE, cancelGraceMs: 100 });
    const closing = pm2.shutdown();
    // A fan-out that was awaiting git when the signal arrived would otherwise spawn a child *after*
    // shutdown took its snapshot of live runs, and nothing would ever reap it.
    await expect(
      pm2.start({ runId: 'r23', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' }),
    ).rejects.toThrow(/shutting down/);
    expect(repo.getRun(db, 'r23')).toBeUndefined();
    await closing;
    // shutting down twice is the same shutdown, not a second one
    expect(pm2.shutdown()).toBe(pm2.shutdown());
    await pm2.shutdown();
  });

  it('records a server-side abort with its own reason, not as a Stop', async () => {
    await pm.start({ runId: 'r24', laneId: 'lane', prompt: 'HANG', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r24'));
    expect(await pm.abort('r24', 'the race could not be started in full')).toBe(true);
    await waitFor(() => terminal('r24'));
    // The operator did not press anything; saying "Stopped by you" would be a lie about who acted.
    expect(last('r24').status).toBe('failed');
    expect(last('r24').error?.message).toBe('the race could not be started in full');
    expect(repo.getRun(db, 'r24')?.errorMessage).toBe('the race could not be started in full');
    expect(await pm.abort('nope', 'x')).toBe(false);
  });

  it('answers a second Stop without starting a second escalation', async () => {
    const stubborn = writeAgent(
      'twice-agent.mjs',
      `process.on('SIGINT', () => {});
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: stubborn, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r16', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.some((e) => e.runId === 'r16'));
      const pid = pm2.pidOf('r16') as number;
      expect(await pm2.cancel('r16')).toBe(true);
      // a second Stop while the first is still escalating is the same request, answered the same way
      expect(await pm2.cancel('r16')).toBe(true);
      await waitFor(() => terminal('r16'));
      await waitFor(() => !alive(pid), 5000);
      expect(last('r16').status).toBe('cancelled');
      // exactly one terminal lifecycle for the run, however many times it was asked to stop
      expect(hub.lifecycles.filter((l) => l.runId === 'r16' && l.status !== 'running')).toHaveLength(1);
      // and once it is gone, a Stop has nothing to reach
      expect(await pm2.cancel('r16')).toBe(false);
    } finally {
      await pm2.shutdown();
    }
  });

  it('leaves no process behind after shutdown, even one that ignores every polite signal', async () => {
    const stubborn = writeAgent(
      'shutdown-agent.mjs',
      `process.on('SIGINT', () => {});
       process.on('SIGTERM', () => {});
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: stubborn, cancelGraceMs: 150 });
    await pm2.start({ runId: 'r17', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r17'));
    const pid = pm2.pidOf('r17') as number;

    await pm2.shutdown();

    expect(alive(pid)).toBe(false);
    expect(pm2.isLive('r17')).toBe(false);
    expect(repo.getRun(db, 'r17')).toMatchObject({ status: 'failed', errorMessage: 'server shutting down' });
  });

  it('still reaps the child when the terminal write itself fails', async () => {
    // A database that cannot be written is exactly when a run most needs its process cleaned up.
    const stubborn = writeAgent(
      'unwritable-agent.mjs',
      `process.on('SIGINT', () => {});
       process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       setInterval(() => {}, 1000);`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: stubborn, cancelGraceMs: 150 });
    try {
      await pm2.start({ runId: 'r18', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => hub.events.some((e) => e.runId === 'r18'));
      const pid = pm2.pidOf('r18') as number;
      // every write to `runs` now fails, including the one that records the outcome
      sqlite.exec(
        "CREATE TRIGGER no_run_writes BEFORE UPDATE ON runs BEGIN SELECT RAISE(FAIL, 'read-only'); END;",
      );

      expect(await pm2.cancel('r18')).toBe(true);

      // the client is still told what happened, from what the manager knows in memory ...
      await waitFor(() => terminal('r18'));
      expect(last('r18').status).toBe('cancelled');
      // ... and the process is still reaped
      await waitFor(() => !alive(pid), 5000);
      expect(alive(pid)).toBe(false);
      sqlite.exec('DROP TRIGGER no_run_writes');
    } finally {
      await pm2.shutdown();
    }
  });

  it('keeps the stderr tail bounded even when it arrives as one huge write', async () => {
    const noisy = writeAgent(
      'very-noisy-agent.mjs',
      `process.stdout.write(${JSON.stringify(`${INIT_LINE}\n`)});
       // exit from the write's callback, so the whole 200 KB really reaches the manager rather
       // than being discarded with the pending pipe writes
       process.stderr.write('X'.repeat(200000) + 'THE-VERY-END\\n', () => process.exit(7));`,
    );
    const pm2 = new ProcessManager({ db, hub, claudeBin: noisy, cancelGraceMs: 200 });
    try {
      await pm2.start({ runId: 'r19', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
      await waitFor(() => terminal('r19'));
      const tail = repo.getRun(db, 'r19')?.stderrTail ?? '';
      expect(tail.length).toBeLessThanOrEqual(4000);
      // the *end* is what is kept: it is the part that says what went wrong
      expect(tail.endsWith('THE-VERY-END\n')).toBe(true);
    } finally {
      await pm2.shutdown();
    }
  });

  it('passes --resume and the fake echoes the session id', async () => {
    await pm.start({
      runId: 'r7',
      laneId: 'lane',
      prompt: 'follow up',
      cwd: tmp.path,
      permission: 'allowlist',
      resumeSessionId: 'my-session',
    });
    await waitFor(() => terminal('r7'));
    expect(last('r7').sessionId).toBe('my-session');
  });

  it('ends every live run visibly when the server shuts down', async () => {
    await pm.start({ runId: 'r9', laneId: 'lane', prompt: 'HANG', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r9'));
    expect(pm.isLive('r9')).toBe(true);

    await pm.shutdown();

    expect(pm.isLive('r9')).toBe(false);
    expect(repo.getRun(db, 'r9')).toMatchObject({
      status: 'failed',
      errorMessage: 'server shutting down',
    });
    expect(last('r9')).toMatchObject({ status: 'failed', error: { message: 'server shutting down' } });
    // the outcome is final: nothing the dying child still writes appends events or revives the run
    const seen = repo.listEvents(db, 'r9').length;
    await wait(150);
    expect(repo.listEvents(db, 'r9')).toHaveLength(seen);
    expect(repo.getRun(db, 'r9')?.status).toBe('failed');
  });

  it('fails the run visibly when an event cannot be persisted, and stops reading', async () => {
    // A duplicate (runId, seq) is the cheapest real `insertEvent` failure: the row is already
    // there, so the run's very first line trips the events primary key.
    repo.insertEvent(db, {
      runId: 'r12',
      seq: 1,
      receivedAt: 1,
      type: 'system',
      parentToolUseId: null,
      json: '{"type":"system","subtype":"init"}',
    });
    await pm.start({ runId: 'r12', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => terminal('r12'));
    expect(last('r12').status).toBe('failed');
    expect(last('r12').error?.message).toMatch(/^could not persist events: /);
    expect(repo.getRun(db, 'r12')?.errorMessage).toMatch(/^could not persist events: /);
    // The outcome is published at once, but the run is still owned until its process is gone: this
    // fake agent stops on SIGINT, so that happens a moment later (the stubborn case is covered
    // above, where the child ignores every polite signal).
    await waitFor(() => !pm.isLive('r12'));
    // reading stopped there: nothing more was appended, and the outcome stands
    const seen = repo.listEvents(db, 'r12').length;
    await wait(120);
    expect(repo.listEvents(db, 'r12')).toHaveLength(seen);
    expect(repo.getRun(db, 'r12')?.status).toBe('failed');
    expect(repo.getRun(db, 'r12')?.errorMessage).toMatch(/^could not persist events: /);
  });

  it('fails cleanly when the executable does not exist', async () => {
    const broken = new ProcessManager({ db, hub, claudeBin: '/nonexistent/claude' });
    await broken.start({ runId: 'r8', laneId: 'lane', prompt: 'x', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => terminal('r8'));
    expect(last('r8').status).toBe('failed');
    expect(last('r8').error?.message).toMatch(/ENOENT|not found/);
    // Nothing was ever started, so there is nothing to own and nothing to signal.
    expect(broken.isLive('r8')).toBe(false);
    expect((await broken.shutdown()).unconfirmed).toEqual([]);
  });

  /**
   * A1. A spawn that never started must never be signalled.
   *
   * Between `spawn()` returning and the `error` event of a failed spawn, the child has no pid but
   * has not exited either. Reading liveness from the exit codes alone calls that "alive" and falls
   * back to signalling the leader — and `child.kill()` on a child whose spawn failed reaches libuv
   * with pid `0`, which POSIX defines as *the caller's own process group*. Cancelling a missing
   * executable therefore killed the server (audit finding 1, reproduced again here at
   * `group.ts:ownGroup`: `kill returned true` and the supervising process took the SIGINT).
   *
   * The probe has to be a process of its own, in a process group of its own, for the same reason:
   * if it regresses it dies, and asserting it in-process would take this suite with it.
   */
  it('cannot signal the supervising process when a missing executable is cancelled and shut down', async () => {
    const probe = fileURLToPath(new URL('../helpers/missing-cli-probe.ts', import.meta.url));
    const result = await new Promise<{ code: number | null; signal: string | null; out: string }>(
      (resolve) => {
        const child = spawn(process.execPath, ['--import', 'tsx', probe], {
          cwd: fileURLToPath(new URL('../..', import.meta.url)),
          stdio: ['ignore', 'pipe', 'pipe'],
          // Its own group: a stray signal to "the process group" reaches the probe's, not ours.
          detached: true,
        });
        let out = '';
        child.stdout?.setEncoding('utf8').on('data', (c: string) => {
          out += c;
        });
        child.stderr?.setEncoding('utf8').on('data', (c: string) => {
          out += c;
        });
        child.on('close', (code, signal) => resolve({ code, signal, out }));
      },
    );
    // It got as far as starting, so the dangerous window really was entered ...
    expect(result.out).toContain('STARTED pid=undefined');
    // ... and it came out the other side rather than being signalled.
    expect(result.signal).toBeNull();
    expect(result.out).toContain('SURVIVED');
    expect(result.code).toBe(0);
    // The run still ends visibly, and shutdown has nothing it could not confirm.
    expect(result.out).toContain('STATUS failed');
    expect(result.out).toContain('SHUTDOWN unconfirmed=0');
  }, 30_000);

  /**
   * A8. Shutdown that cannot confirm cleanup says which runs, rather than resolving quietly.
   *
   * Exercised through the signalling seam with an `EPERM` answer to the liveness question — the
   * unconfirmable OS boundary — while real signals are still delivered, so the test leaves no
   * process behind.
   */
  it('reports the process groups it could not confirm gone, and keeps saying why afterwards', async () => {
    const pm2 = new ProcessManager({
      db,
      hub,
      claudeBin: FAKE,
      cancelGraceMs: 20,
      control: {
        kill(pid, signal) {
          // "The group is there, but not yours to signal." Alive, as far as anyone can tell.
          if (signal === 0) throw Object.assign(new Error('not permitted'), { code: 'EPERM' });
          process.kill(pid, signal);
        },
      },
    });
    await pm2.start({ runId: 'r29', laneId: 'lane', prompt: 'HANG', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r29'));
    const pid = pm2.pidOf('r29') as number;

    const report = await pm2.shutdown();

    expect(report.unconfirmed).toEqual([{ runId: 'r29', pgid: pid }]);
    // The process really did go — only the *confirmation* was impossible.
    await waitFor(() => !alive(pid), 5000);
    // Ownership is over (the server is leaving; nothing is left to escalate and no timer is armed),
    // but the reason survives for whoever reports the shutdown.
    expect(pm2.isLive('r29')).toBe(false);
    expect(pm2.cleanupErrorOf('r29')).toMatch(/could not confirm/);
    // Shutting down again is the same shutdown and says the same thing.
    expect(await pm2.shutdown()).toEqual(report);
  }, 30_000);

  it('reports nothing unconfirmed for an ordinary shutdown', async () => {
    await pm.start({ runId: 'r30', laneId: 'lane', prompt: 'HANG', cwd: tmp.path, permission: 'allowlist' });
    await waitFor(() => hub.events.some((e) => e.runId === 'r30'));
    expect(await pm.shutdown()).toEqual({ unconfirmed: [] });
    expect(pm.cleanupErrorOf('r30')).toBeUndefined();
  });
});

describe('outcomeStatus', () => {
  const clean = { sawResult: true, resultIsError: false, code: 0 };
  it('a clean run is finished, cancelled or not, depending on what came first', () => {
    expect(outcomeStatus({ ...clean, cancelRequested: false, resultBeforeCancel: true })).toBe('finished');
    // Stop raced a run that had already reported its result: keep the result.
    expect(outcomeStatus({ ...clean, cancelRequested: true, resultBeforeCancel: true })).toBe('finished');
    // Stop came first and the agent wrapped up in answer to it: the user stopped this run.
    expect(outcomeStatus({ ...clean, cancelRequested: true, resultBeforeCancel: false })).toBe('cancelled');
  });
  it('anything unclean is cancelled when asked for, failed otherwise', () => {
    const unclean = { sawResult: true, resultIsError: true, resultBeforeCancel: false, code: 0 };
    expect(outcomeStatus({ ...unclean, cancelRequested: true })).toBe('cancelled');
    expect(outcomeStatus({ ...unclean, cancelRequested: false })).toBe('failed');
    expect(outcomeStatus({ ...clean, code: 1, cancelRequested: false, resultBeforeCancel: true })).toBe(
      'failed',
    );
    expect(
      outcomeStatus({
        sawResult: false,
        resultIsError: false,
        code: 0,
        cancelRequested: false,
        resultBeforeCancel: false,
      }),
    ).toBe('failed');
  });
});
