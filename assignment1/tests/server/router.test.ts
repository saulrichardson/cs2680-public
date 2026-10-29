import { chmodSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { loadConfig } from '@/server/config';
import { createServerContext, type ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { activeOperation, createCaller } from '@/server/trpc/router';
import { readFixtureLines } from '../helpers/fixtures';
import { makeTmpDir, makeTmpGitRepo } from '../helpers/tmp';

const FAKE = fileURLToPath(new URL('../fake-claude/claude', import.meta.url));
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function until(pred: () => boolean, ms = 10_000) {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error('timeout');
    await wait(10);
  }
}

let ctx: ServerContext;
let caller: ReturnType<typeof createCaller>;
const tmp = makeTmpDir();
const scratch = makeTmpGitRepo();

beforeAll(() => {
  ctx = createServerContext(
    loadConfig(
      {
        MISSION_CONTROL_DATA_DIR: tmp.path,
        MISSION_CONTROL_CLAUDE_BIN: FAKE,
      } as unknown as NodeJS.ProcessEnv,
      tmp.path,
    ),
  );
  caller = createCaller(ctx);
});
afterAll(async () => {
  await ctx.close();
  tmp.cleanup();
  scratch.cleanup();
});

describe('appRouter', () => {
  it('notifies other tabs after creating, renaming, archiving, and reopening a session', async () => {
    const changed = vi.spyOn(ctx.hub, 'publishLanesChanged');
    try {
      const lane = await caller.lanes.create({ cwd: scratch.path, name: 'Shared session' });
      await caller.lanes.update({ laneId: lane.id, name: 'Renamed session' });
      await caller.lanes.archive({ laneId: lane.id });
      await caller.lanes.reopen({ laneId: lane.id });
      expect(changed).toHaveBeenCalledTimes(4);
    } finally {
      changed.mockRestore();
    }
  });
  it('creates lanes, starts a run, and exposes events', async () => {
    const lane = await caller.lanes.create({ name: 'Lane 1', cwd: scratch.path });
    expect(lane).toMatchObject({ name: 'Lane 1', cwd: scratch.path, permission: 'allowlist' });
    // `FIXTURE:flat` pins the recording whose numbers this test asserts (the fake CLI otherwise
    // picks one from the prompt's wording).
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat fix the test' });
    await until(() => ['finished', 'failed'].includes(ctx.runStatus(runId) ?? ''));
    const run = await caller.runs.get({ runId });
    expect(run.status).toBe('finished');
    expect(run.numbers?.numTurns).toBe(12);
    const events = await caller.runs.events({ runId });
    expect(events).toHaveLength(73);
    const tail = await caller.runs.events({ runId, afterSeq: 70 });
    expect(tail.map((e) => e.seq)).toEqual([71, 72, 73]);
    expect((await caller.runs.list({ laneId: lane.id })).map((r) => r.runId)).toEqual([runId]);
  });

  it('resumes with the lane latest session id and rejects resume without one', async () => {
    const lane = await caller.lanes.create({ name: 'Lane 2', cwd: scratch.path });
    await expect(caller.runs.resume({ laneId: lane.id, prompt: 'more' })).rejects.toThrow(/no session/);
    const first = await caller.runs.start({ laneId: lane.id, prompt: 'start' });
    await until(() => ctx.runStatus(first.runId) === 'finished');
    const second = await caller.runs.resume({ laneId: lane.id, prompt: 'continue' });
    await until(() => ctx.runStatus(second.runId) === 'finished');
    const run = await caller.runs.get({ runId: second.runId });
    expect(run.resumedFrom).toBe(first.runId);
    expect(run.sessionId).toBe('066df07f-cf42-4d5a-b156-e83d817ad021');
  });

  it('fails a run against a bad directory visibly', async () => {
    const lane = await caller.lanes.create({ name: 'Bad', cwd: '/bad/path' });
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'anything' });
    const run = await caller.runs.get({ runId });
    expect(run.status).toBe('failed');
    expect(run.error?.message).toContain('/bad/path is not a directory');
  });

  it('cancels a hanging run', async () => {
    const lane = await caller.lanes.create({ name: 'Hang', cwd: scratch.path });
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'HANG' });
    await wait(100);
    expect(await caller.runs.cancel({ runId })).toEqual({ cancelled: true });
    await until(() => ctx.runStatus(runId) === 'cancelled');
  });

  it('fans out into worktrees, compares, and keeps a result', async () => {
    const { groupId, lanes } = await caller.fanout.start({
      repoRoot: scratch.path,
      prompt: 'FIXTURE:flat add a flag',
      laneCount: 2,
    });
    expect(lanes).toHaveLength(2);
    expect(existsSync(join(tmp.path, 'worktrees', groupId))).toBe(true);
    await until(() => lanes.every((l) => ctx.runStatus(l.runId) === 'finished'));
    writeFileSync(join(tmp.path, 'worktrees', groupId, '0', 'README.md'), '# changed by lane 0\n');
    const cmp = await caller.fanout.compare({ groupId });
    expect(cmp.lanes.map((l) => l.files.length)).toEqual([1, 0]);
    expect(cmp.lanes[0]?.summary?.callCount).toBe(10);
    const candidate = lanes[0];
    if (!candidate) throw new Error('expected a candidate');
    await caller.fanout.keep({ groupId, runId: candidate.runId });
    expect((await caller.fanout.get({ groupId })).keptRunId).toBe(candidate.runId);
    await expect(
      caller.fanout.start({ repoRoot: tmp.path, prompt: 'x', laneCount: 2 }),
    ).rejects.toMatchObject({
      code: 'BAD_REQUEST',
      message: expect.stringContaining('is not a git repository'),
    });
    await expect(
      caller.fanout.start({ repoRoot: scratch.path, prompt: 'x', laneCount: 1 }),
    ).rejects.toThrow();
    await expect(caller.fanout.compare({ groupId: 'no-such-group' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
    });
  });

  it('replays and imports', async () => {
    const lane = await caller.lanes.create({ name: 'Replay', cwd: scratch.path });
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'src' });
    await until(() => ctx.runStatus(runId) === 'finished');
    const replay = await caller.replay.start({ sourceRunId: runId, speed: 'instant' });
    expect((await caller.runs.get({ runId: replay.runId })).replayOf).toBe(runId);
    const imported = await caller.replay.import({
      laneId: lane.id,
      contents: readFixtureLines('failed').join('\n'),
      label: 'failed.jsonl',
    });
    expect((await caller.runs.get({ runId: imported.runId })).status).toBe('failed');
    await expect(caller.replay.start({ sourceRunId: 'no-such-run', speed: 'instant' })).rejects.toMatchObject(
      {
        code: 'NOT_FOUND',
      },
    );
  });

  it('never resumes a recording that was only imported into the lane', async () => {
    const lane = await caller.lanes.create({ name: 'Imported', cwd: scratch.path });
    const imported = await caller.replay.import({
      laneId: lane.id,
      contents: readFixtureLines('flat').join('\n'),
      label: 'events.jsonl',
    });
    const dto = await caller.runs.get({ runId: imported.runId });
    expect(dto.status).toBe('finished');
    // the recording's own session id stays in its init event, not on the run row
    expect(dto.sessionId).toBeUndefined();
    expect(dto.resumable).toBe(false);
    await expect(caller.runs.resume({ laneId: lane.id, prompt: 'more' })).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
    });

    // ... while a run the lane actually executed is resumable
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat go' });
    await until(() => ctx.runStatus(runId) === 'finished');
    expect((await caller.runs.get({ runId })).resumable).toBe(true);
    const second = await caller.runs.resume({ laneId: lane.id, prompt: 'continue' });
    await until(() => ctx.runStatus(second.runId) === 'finished');
    expect((await caller.runs.get({ runId: second.runId })).resumedFrom).toBe(runId);
  });

  /**
   * Archiving is not a way to stop work, and must never quietly become one. The old `close`
   * cancelled whatever the session was running, which made putting a session away and killing an
   * agent the same click.
   */
  it('refuses to archive a session it is still driving, and leaves the run alone', async () => {
    const lane = await caller.lanes.create({ name: 'Busy', cwd: scratch.path });
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'HANG' });
    await until(() => ctx.processes.isLive(runId));

    await expect(caller.lanes.archive({ laneId: lane.id })).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
      message: expect.stringContaining('still running'),
    });
    // Nothing was cancelled on the way to that refusal.
    expect(ctx.processes.isLive(runId)).toBe(true);
    expect(ctx.runStatus(runId)).toBe('running');
    expect((await caller.lanes.list()).some((l) => l.id === lane.id)).toBe(true);

    // Stop is the explicit action; archiving is then allowed, and keeps the run.
    await caller.runs.cancel({ runId });
    await until(() => !ctx.processes.isLive(runId));
    expect((await caller.lanes.archive({ laneId: lane.id })).archivedAt).toBeGreaterThan(0);
    expect((await caller.lanes.list()).some((l) => l.id === lane.id)).toBe(false);
    expect((await caller.runs.list({ laneId: lane.id })).map((r) => r.runId)).toEqual([runId]);

    // ... and it comes back with its history.
    expect((await caller.lanes.reopen({ laneId: lane.id })).archivedAt).toBeNull();
    expect((await caller.lanes.list()).some((l) => l.id === lane.id)).toBe(true);
    await expect(caller.lanes.archive({ laneId: 'no-such-lane' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
    });
  });

  it('refuses to keep a result for an unknown fan-out group', async () => {
    await expect(caller.fanout.keep({ groupId: 'no-such-group', runId: null })).rejects.toMatchObject({
      code: 'NOT_FOUND',
    });
  });

  // chmod 000 does not deny root, so the unreadable half of this test is skipped when run as root.
  it.skipIf(process.getuid?.() === 0)('tells a missing directory apart from an unreadable one', async () => {
    const missing = await caller.fs.browse({ path: join(scratch.path, 'nope') });
    expect(missing).toMatchObject({ exists: false, error: 'does not exist', dirs: [] });

    const locked = makeTmpDir('mc-locked-');
    try {
      chmodSync(locked.path, 0o000);
      const res = await caller.fs.browse({ path: locked.path });
      expect(res.error).toBe('is not readable');
      expect(res.dirs).toEqual([]);
    } finally {
      chmodSync(locked.path, 0o700);
      locked.cleanup();
    }

    expect((await caller.fs.browse({ path: scratch.path })).error).toBeUndefined();
  });

  it('refuses the home directory and the filesystem root as a lane directory', async () => {
    const message = expect.stringContaining('not your home folder or the filesystem root');
    for (const cwd of [homedir(), `${homedir()}/`, '/']) {
      await expect(caller.lanes.create({ name: 'Nope', cwd })).rejects.toMatchObject({
        code: 'BAD_REQUEST',
        message,
      });
    }
    await expect(
      caller.fanout.start({ repoRoot: homedir(), prompt: 'x', laneCount: 2 }),
    ).rejects.toMatchObject({ code: 'BAD_REQUEST', message });
    // a real project directory underneath home is still fine
    const ok = await caller.lanes.create({ name: 'Fine', cwd: scratch.path });
    expect(ok.cwd).toBe(scratch.path);
  });

  it('browses directories for the picker', async () => {
    const res = await caller.fs.browse({ path: scratch.path });
    expect(res.isGitRepo).toBe(true);
    expect(res.dirs).toContain('src');
    expect(res.parent).toBe(join(scratch.path, '..'));
    expect(res.truncated).toBe(false);
  });

  it('says so when a directory listing hits the cap', async () => {
    const many = makeTmpDir('mc-many-');
    try {
      for (let i = 0; i < 501; i += 1) mkdirSync(join(many.path, `d${String(i).padStart(4, '0')}`));
      const res = await caller.fs.browse({ path: many.path });
      expect(res.dirs).toHaveLength(500);
      expect(res.truncated).toBe(true);
      expect(res.dirs[0]).toBe('d0000');
    } finally {
      many.cleanup();
    }
  });

  /**
   * R2. A lane runs one operation at a time, whatever kind it is. Before this, `replay.import` and
   * `replay.start` had no lane guard at all: a recording dropped into a busy lane became the newest
   * row, and the console's Stop — which targeted the newest row — cancelled a finished recording
   * while the agent kept working in the directory.
   */
  it('refuses a replay or an import into a lane that is already running something', async () => {
    const source = await caller.lanes.create({ name: 'Recording source', cwd: scratch.path });
    const recorded = await caller.runs.start({ laneId: source.id, prompt: 'FIXTURE:flat record me' });
    await until(() => ctx.runStatus(recorded.runId) === 'finished');

    const lane = await caller.lanes.create({ name: 'Busy with an agent', cwd: scratch.path });
    const working = await caller.runs.start({ laneId: lane.id, prompt: 'HANG' });
    await until(() => ctx.processes.isLive(working.runId));

    const busy = { code: 'PRECONDITION_FAILED', message: 'A run is already in progress in this lane' };
    await expect(
      caller.replay.import({ laneId: lane.id, contents: readFixtureLines('flat').join('\n') }),
    ).rejects.toMatchObject(busy);
    await expect(
      caller.replay.start({ sourceRunId: recorded.runId, speed: 'instant', laneId: lane.id }),
    ).rejects.toMatchObject(busy);

    // the agent is untouched, and the lane still holds exactly the one run it started with
    expect(ctx.processes.isLive(working.runId)).toBe(true);
    expect((await caller.runs.list({ laneId: lane.id })).map((r) => r.runId)).toEqual([working.runId]);
    // and the lane's one active operation is that agent, which is what Stop must reach
    expect(activeOperation(ctx, lane.id)?.id).toBe(working.runId);
    expect(await caller.runs.cancel({ runId: working.runId })).toEqual({ cancelled: true });
    await until(() => ctx.runStatus(working.runId) === 'cancelled');

    // once the agent is gone the same recording lands
    const imported = await caller.replay.import({
      laneId: lane.id,
      contents: readFixtureLines('flat').join('\n'),
    });
    expect((await caller.runs.get({ runId: imported.runId })).origin).toBe('import');
  });

  it('records what made each run, and refuses to resume or cancel a recording', async () => {
    const lane = await caller.lanes.create({ name: 'Provenance', cwd: scratch.path });
    const executed = await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat do it' });
    await until(() => ctx.runStatus(executed.runId) === 'finished');
    const replayed = await caller.replay.start({ sourceRunId: executed.runId, speed: 'instant' });
    const imported = await caller.replay.import({
      laneId: lane.id,
      contents: readFixtureLines('flat').join('\n'),
      label: 'events.jsonl',
    });

    const origins = Object.fromEntries(
      (await caller.runs.list({ laneId: lane.id })).map((r) => [r.runId, r.origin]),
    );
    expect(origins[executed.runId]).toBe('execution');
    expect(origins[replayed.runId]).toBe('replay');
    expect(origins[imported.runId]).toBe('import');

    // A replay carries the recording's session id now (it is a faithful copy of the run), and is
    // still never what a follow-up resumes — `origin` decides that, not the presence of a session.
    const replayDto = await caller.runs.get({ runId: replayed.runId });
    expect(replayDto.sessionId).toBe('066df07f-cf42-4d5a-b156-e83d817ad021');
    expect(replayDto.resumable).toBe(false);
    expect((await caller.runs.get({ runId: imported.runId })).resumable).toBe(false);

    // the follow-up goes to the run this lane actually executed
    const followUp = await caller.runs.resume({ laneId: lane.id, prompt: 'and now this' });
    await until(() => ctx.runStatus(followUp.runId) === 'finished');
    expect((await caller.runs.get({ runId: followUp.runId })).resumedFrom).toBe(executed.runId);

    // nothing is driving a finished recording, so there is nothing to stop
    expect(await caller.runs.cancel({ runId: imported.runId })).toEqual({ cancelled: false });
    expect(await caller.runs.cancel({ runId: replayed.runId })).toEqual({ cancelled: false });
    await expect(caller.runs.cancel({ runId: 'no-such-run' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
    });
  });

  it('stops a paced replay through the same Stop the agent uses', async () => {
    const lane = await caller.lanes.create({ name: 'Playback', cwd: scratch.path });
    const executed = await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat record' });
    await until(() => ctx.runStatus(executed.runId) === 'finished');
    const playing = await caller.replay.start({ sourceRunId: executed.runId, speed: '4x' });
    await until(() => ctx.replayer.isLive(playing.runId));
    // while it plays, the lane is busy — a second operation would leave two live runs in one lane
    expect(activeOperation(ctx, lane.id)?.id).toBe(playing.runId);
    await expect(caller.runs.start({ laneId: lane.id, prompt: 'me too' })).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
    });
    expect(await caller.runs.cancel({ runId: playing.runId })).toEqual({ cancelled: true });
    expect(ctx.runStatus(playing.runId)).toBe('cancelled');
    // and the lane is free again
    expect(activeOperation(ctx, lane.id)).toBeUndefined();
  });

  /**
   * A race is described in one transaction, before any agent exists. Written row by row alongside
   * the spawning, a failure part-way left lanes with no worktree record and a group with fewer
   * lanes than there were worktrees on disk — a race Compare could not account for.
   */
  it('leaves nothing behind when a race cannot be recorded', async () => {
    const before = {
      groups: (await caller.fanout.list()).length,
      lanes: (await caller.lanes.list()).length,
    };
    // the second lane insert fails, part-way through describing the race
    ctx.sqlite.exec(
      "CREATE TRIGGER reject_second_lane BEFORE INSERT ON lanes WHEN NEW.name = 'Agent 2' BEGIN SELECT RAISE(FAIL, 'disk full'); END;",
    );
    try {
      await expect(
        caller.fanout.start({ repoRoot: scratch.path, prompt: 'FIXTURE:flat doomed', laneCount: 2 }),
      ).rejects.toThrow(/Could not record the race/);
    } finally {
      ctx.sqlite.exec('DROP TRIGGER reject_second_lane');
    }

    // no half-described race ...
    expect((await caller.fanout.list()).length).toBe(before.groups);
    expect((await caller.lanes.list()).length).toBe(before.lanes);
    // ... and no worktrees either, because at that point nothing had run in them
    expect(existsSync(join(tmp.path, 'worktrees'))).toBe(true); // the parent stays
    const stale = await caller.fanout.list();
    expect(stale.every((g) => existsSync(join(tmp.path, 'worktrees', g.id)))).toBe(true);

    // and the same request works once the failure is gone
    const ok = await caller.fanout.start({
      repoRoot: scratch.path,
      prompt: 'FIXTURE:flat recovered',
      laneCount: 2,
    });
    expect(ok.lanes).toHaveLength(2);
    await until(() => ok.lanes.every((l) => ctx.runStatus(l.runId) === 'finished'));
  });

  it('stops the agents it did start when a later one cannot start, with the reason', async () => {
    // The third run row cannot be written, so the third agent never starts. The first two are
    // already working in worktrees they may have touched, so nothing is deleted — they are stopped,
    // and the whole race stays visible as a failure.
    let seen = 0;
    const original = ctx.processes.start.bind(ctx.processes);
    ctx.processes.start = async (input) => {
      seen += 1;
      if (seen === 3) throw new Error('no room to record this run');
      return original(input);
    };
    try {
      await expect(
        caller.fanout.start({ repoRoot: scratch.path, prompt: 'HANG partial', laneCount: 3 }),
      ).rejects.toThrow(/could not be started in full/);
    } finally {
      ctx.processes.start = original;
    }

    const group = (await caller.fanout.list())[0];
    if (!group) throw new Error('no race was recorded');
    const lanes = (await caller.fanout.get({ groupId: group.id })).lanes;
    // the race is still there, described in full, with its worktrees intact
    expect(lanes).toHaveLength(3);
    for (const lane of lanes) expect(existsSync(lane.cwd)).toBe(true);

    // the two that did start are stopped, and say why — not "Stopped by you"
    const runs = (await Promise.all(lanes.map((l) => caller.runs.list({ laneId: l.id })))).flat();
    const stopped = runs.filter((r) => r.error?.message.includes('could not be started in full'));
    expect(stopped.length).toBeGreaterThanOrEqual(2);
    for (const run of stopped) expect(run.status).toBe('failed');
    for (const lane of lanes) await caller.lanes.archive({ laneId: lane.id }).catch(() => undefined);
  });

  it('accepts no new work in an archived session, until it is reopened', async () => {
    const lane = await caller.lanes.create({ name: 'Shut', cwd: scratch.path });
    await caller.lanes.archive({ laneId: lane.id });
    const archived = {
      code: 'PRECONDITION_FAILED',
      message: 'This session is archived. Reopen it to continue.',
    };
    await expect(caller.runs.start({ laneId: lane.id, prompt: 'after' })).rejects.toMatchObject(archived);
    await expect(caller.runs.resume({ laneId: lane.id, prompt: 'after' })).rejects.toMatchObject(archived);
    await expect(
      caller.replay.import({ laneId: lane.id, contents: readFixtureLines('flat').join('\n') }),
    ).rejects.toMatchObject(archived);
    // Its files are still readable while archived — archiving hides, it does not remove.
    expect((await caller.outputs.list({ laneId: lane.id })).root).toBe(scratch.path);
    await caller.lanes.reopen({ laneId: lane.id });
    await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat after reopening' });
  });

  /**
   * A session's folder is fixed at creation. Moving one was only ever needed by the board's
   * inline editor, and for a session with a managed checkout it would have pointed later runs
   * somewhere else while the checkout went on existing with nothing aimed at it. Renaming is what
   * is left, and it is the only thing `update` does.
   */
  it('renames a session and cannot move it', async () => {
    const lane = await caller.lanes.create({ name: 'Before', cwd: scratch.path });
    expect((await caller.lanes.update({ laneId: lane.id, name: 'After' })).name).toBe('After');
    expect((await caller.lanes.list()).find((l) => l.id === lane.id)?.cwd).toBe(scratch.path);
  });

  it('keeps only a result that belongs to the race', async () => {
    // Membership is a rule about saved runs; executing agents is covered by the fan-out smoke test.
    const groupId = 'membership';
    repo.createGroup(ctx.db, {
      id: groupId,
      repoRoot: scratch.path,
      prompt: 'Compare results',
      baseCommit: 'unused',
      createdAt: 1,
    });
    for (const id of ['member', 'outsider']) {
      repo.createLane(ctx.db, {
        id,
        name: id,
        cwd: scratch.path,
        permission: 'allowlist',
        createdAt: 1,
        groupId: id === 'member' ? groupId : null,
      });
      repo.createRun(ctx.db, {
        id,
        laneId: id,
        prompt: 'Saved result',
        effectiveCwd: scratch.path,
        permission: 'allowlist',
        origin: 'execution',
        status: 'finished',
        startedAt: 1,
        endedAt: 2,
      });
    }
    await expect(caller.fanout.keep({ groupId, runId: 'outsider' })).rejects.toMatchObject({
      code: 'BAD_REQUEST',
    });
    await expect(caller.fanout.keep({ groupId, runId: 'no-such-run' })).rejects.toMatchObject({
      code: 'BAD_REQUEST',
    });
    // one of the race's own runs is fine, and clearing it is always allowed
    await caller.fanout.keep({ groupId, runId: 'member' });
    expect((await caller.fanout.get({ groupId })).keptRunId).toBe('member');
    await caller.fanout.keep({ groupId, runId: null });
    expect((await caller.fanout.get({ groupId })).keptRunId).toBeNull();
  });

  it('answers a missing lane, run or race with a message a person can read', async () => {
    await expect(caller.runs.get({ runId: 'nope' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
      message: 'That run no longer exists',
    });
    await expect(caller.runs.events({ runId: 'nope' })).rejects.toMatchObject({
      message: 'That run no longer exists',
    });
    await expect(caller.runs.start({ laneId: 'nope', prompt: 'x' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
      message: 'That lane no longer exists',
    });
    await expect(caller.lanes.update({ laneId: 'nope', name: 'x' })).rejects.toMatchObject({
      message: 'That lane no longer exists',
    });
    await expect(caller.lanes.archive({ laneId: 'nope' })).rejects.toMatchObject({
      message: 'That lane no longer exists',
    });
    await expect(caller.fanout.compare({ groupId: 'nope' })).rejects.toMatchObject({
      code: 'NOT_FOUND',
      message: 'That race no longer exists',
    });
    await expect(caller.fanout.get({ groupId: 'nope' })).rejects.toMatchObject({
      message: 'That race no longer exists',
    });
    await expect(caller.replay.import({ laneId: 'nope', contents: '{}' })).rejects.toMatchObject({
      message: 'That lane no longer exists',
    });

    // a replay into a lane that has since been closed is a missing lane, not a missing run
    const lane = await caller.lanes.create({ name: 'Replay target', cwd: scratch.path });
    const { runId } = await caller.runs.start({ laneId: lane.id, prompt: 'FIXTURE:flat source' });
    await until(() => ctx.runStatus(runId) === 'finished');
    await expect(
      caller.replay.start({ sourceRunId: runId, speed: 'instant', laneId: 'no-such-lane' }),
    ).rejects.toMatchObject({ code: 'NOT_FOUND', message: 'That lane no longer exists' });
    // ... and into its own lane it still works
    const replay = await caller.replay.start({ sourceRunId: runId, speed: 'instant', laneId: lane.id });
    expect((await caller.runs.get({ runId: replay.runId })).replayOf).toBe(runId);
  });

  it('reports the home directory and the Chrome setup hint, and nothing else, from system.info', async () => {
    const info = await caller.system.info();
    // The shape is pinned rather than the values: the agent binary, the data directory and the
    // live-run list were never the browser's business and must not creep back in. `chrome` is the
    // one addition — a before-you-start hint about this machine's native messaging host, whose
    // contents depend on what is installed and so are only checked for type.
    expect(Object.keys(info).sort()).toEqual(['chrome', 'home']);
    expect(info.home).toBe(homedir());
    expect(typeof info.chrome.checked).toBe('boolean');
    expect(typeof info.chrome.installed).toBe('boolean');
  });
});
