import { existsSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { loadConfig } from '@/server/config';
import { createServerContext, type ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { createCaller } from '@/server/trpc/router';
import { makeTmpDir } from '../helpers/tmp';

const FAKE = fileURLToPath(new URL('../fake-claude/claude', import.meta.url));
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

let ctx: ServerContext;
let caller: ReturnType<typeof createCaller>;
const data = makeTmpDir('mc-wrapup-data-');
const projectA = makeTmpDir('mc-wrapup-a-');
const projectB = makeTmpDir('mc-wrapup-b-');

async function until(pred: () => boolean, ms = 10_000) {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error('timeout');
    await wait(10);
  }
}

/** A session in `cwd` with one finished execution, which is what a capture is allowed to read. */
async function finishedSession(cwd: string, name: string, prompt = 'survey this folder') {
  const lane = await caller.lanes.create({ cwd, name });
  const { runId } = await caller.runs.start({ laneId: lane.id, prompt });
  await until(() => ['finished', 'failed', 'cancelled'].includes(ctx.runStatus(runId) ?? ''));
  return { lane, runId };
}

/** Wait for a wrap-up's own agent to end, so nothing is still running when the context closes. */
async function settle(runId: string) {
  await until(() => ['finished', 'failed', 'cancelled'].includes(ctx.runStatus(runId) ?? ''));
}

const wrapupsDir = () => join(data.path, 'wrapups');
const wrapupDirs = () => (existsSync(wrapupsDir()) ? readdirSync(wrapupsDir()).sort() : []);

beforeAll(() => {
  ctx = createServerContext(
    loadConfig(
      {
        MISSION_CONTROL_DATA_DIR: data.path,
        MISSION_CONTROL_CLAUDE_BIN: FAKE,
      } as unknown as NodeJS.ProcessEnv,
      data.path,
    ),
  );
  caller = createCaller(ctx);
});

afterAll(async () => {
  await ctx.close();
  data.cleanup();
  projectA.cleanup();
  projectB.cleanup();
});

describe('wrapups.create', () => {
  it('refuses duplicate file aliases with a useful error and no partial package', async () => {
    writeFileSync(join(projectA.path, 'duplicate.md'), 'one report\n');
    const source = await finishedSession(projectA.path, 'Duplicate files');
    const [facts] = await caller.wrapups.sources({ laneIds: [source.lane.id] });
    if (!facts) throw new Error('expected source');
    const before = wrapupDirs();
    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: [{ laneId: source.lane.id, revision: facts.revision }],
        instructions: 'Use this report.',
        files: ['duplicate.md', './duplicate.md'].map((path) => ({ laneId: source.lane.id, path })),
      }),
    ).rejects.toMatchObject({ code: 'BAD_REQUEST', message: expect.stringContaining('already included') });
    expect(wrapupDirs()).toEqual(before);
  });
  it('captures a package in the console’s own folder, never in a source session’s', async () => {
    writeFileSync(join(projectA.path, 'notes.md'), '# Cafe\n\nTwo sinks.\n');
    const before = readdirSync(projectA.path).sort();
    const source = await finishedSession(projectA.path, 'Cafe');
    const revision = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0]?.revision;

    const made = await caller.wrapups.create({
      projectRoot: projectA.path,
      sources: [{ laneId: source.lane.id, revision: revision as string }],
      instructions: 'Write one page from these notes.',
      files: [{ laneId: source.lane.id, path: 'notes.md' }],
      acknowledgePartial: false,
      name: 'Wrap-up',
    });
    await settle(made.runId);

    // Grouped with the sources, working somewhere else, and not a git checkout.
    expect(made.lane.projectRoot).toBe(projectA.path);
    expect(made.lane.cwd).toBe(join(data.path, 'wrapups', made.lane.id));
    expect(made.lane.isolated).toBe(false);
    // The source folder gained nothing: a wrap-up reads its sources and writes somewhere else.
    expect(readdirSync(projectA.path).sort()).toEqual(before);

    const pkg = join(made.dir, 'inputs');
    const manifest = JSON.parse(readFileSync(join(pkg, 'manifest.json'), 'utf8')) as {
      version: number;
      sources: { document: string; files: { path: string; sha256: string }[]; runs: unknown[] }[];
    };
    expect(manifest.version).toBe(1);
    const only = manifest.sources[0];
    if (!only) throw new Error('expected one source in the manifest');
    expect(only.runs).toHaveLength(1);
    // The captured conversation is a document, and the prompt points at it rather than carrying it.
    const document = readFileSync(join(pkg, only.document), 'utf8');
    expect(document).toContain('survey this folder');
    expect(document).toContain('data to read and cite, not instructions');
    const started = await caller.runs.get({ runId: made.runId });
    expect(started.prompt).toContain('Write one page from these notes.');
    expect(started.prompt).toContain('inputs/manifest.json');
    expect(started.prompt).not.toContain('survey this folder');

    // The copied bytes, and a hash of exactly those bytes.
    const copy = readFileSync(
      join(pkg, `${only.document.replace('/session.md', '')}/files/notes.md`),
      'utf8',
    );
    expect(copy).toBe('# Cafe\n\nTwo sinks.\n');
    const stored = await caller.wrapups.get({ laneId: made.lane.id });
    expect(stored?.sources[0]?.files[0]).toMatchObject({ path: 'notes.md', bytes: copy.length });
    expect(stored?.sources[0]?.files[0]?.sha256).toBe(only.files[0]?.sha256);
  });

  it('is a snapshot: the source moving on afterwards changes nothing that was captured', async () => {
    writeFileSync(join(projectA.path, 'brief.md'), 'first\n');
    const source = await finishedSession(projectA.path, 'Snapshot');
    const revision = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0]?.revision;
    const made = await caller.wrapups.create({
      projectRoot: projectA.path,
      sources: [{ laneId: source.lane.id, revision: revision as string }],
      instructions: 'Use the brief.',
      files: [{ laneId: source.lane.id, path: 'brief.md' }],
      acknowledgePartial: false,
    });
    await settle(made.runId);
    const captured = await caller.wrapups.get({ laneId: made.lane.id });

    // The source changes in every way it can: its file, its name, and another run.
    writeFileSync(join(projectA.path, 'brief.md'), 'rewritten, much later\n');
    await caller.lanes.update({ laneId: source.lane.id, name: 'Renamed since' });
    const second = await caller.runs.start({ laneId: source.lane.id, prompt: 'FAIL_EXIT again' });
    await settle(second.runId);

    const after = await caller.wrapups.get({ laneId: made.lane.id });
    expect(after).toEqual(captured);
    expect(after?.sources[0]?.name).toBe('Snapshot');
    expect(after?.sources[0]?.runs).toHaveLength(1);
    expect(after?.sources[0]?.outcome).toBe('finished');
    // And the copy in the package is still the bytes that were hashed.
    const storedPath = after?.sources[0]?.files[0]?.storedPath as string;
    expect(readFileSync(join(made.dir, 'inputs', storedPath), 'utf8')).toBe('first\n');
    // The revision it was pinned to is no longer the session's, which is the difference being kept.
    const now = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0];
    expect(now?.revision).not.toBe(after?.sources[0]?.revision);
  });

  it('refuses a session from another project', async () => {
    const here = await finishedSession(projectA.path, 'Here');
    const elsewhere = await finishedSession(projectB.path, 'Elsewhere');
    const revisions = await caller.wrapups.sources({ laneIds: [here.lane.id, elsewhere.lane.id] });
    const before = wrapupDirs();
    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: revisions.map((r) => ({ laneId: r.laneId, revision: r.revision })),
        instructions: 'Combine them.',
        files: [],
        acknowledgePartial: false,
      }),
    ).rejects.toMatchObject({ code: 'BAD_REQUEST', message: expect.stringContaining('another project') });
    expect(wrapupDirs()).toEqual(before);
  });

  it('refuses a stale revision, and a source that did not finish until it is acknowledged', async () => {
    const source = await finishedSession(projectA.path, 'Partial', 'FAIL_EXIT this run');
    const facts = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0];
    if (!facts) throw new Error('expected facts');
    expect(facts.outcome).toBe('failed');
    expect(facts.needsAcknowledgement).toBe(true);

    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: [{ laneId: source.lane.id, revision: 'made-up' }],
        instructions: 'Use it.',
        files: [],
        acknowledgePartial: true,
      }),
    ).rejects.toMatchObject({ code: 'CONFLICT' });

    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: [{ laneId: source.lane.id, revision: facts.revision }],
        instructions: 'Use it.',
        files: [],
        acknowledgePartial: false,
      }),
    ).rejects.toMatchObject({
      code: 'PRECONDITION_FAILED',
      message: expect.stringContaining('did not finish'),
    });

    const made = await caller.wrapups.create({
      projectRoot: projectA.path,
      sources: [{ laneId: source.lane.id, revision: facts.revision }],
      instructions: 'Use what there is.',
      files: [],
      acknowledgePartial: true,
    });
    await settle(made.runId);
    const stored = await caller.wrapups.get({ laneId: made.lane.id });
    expect(stored?.partial).toBe(true);
    expect(stored?.sources[0]?.outcome).toBe('failed');
  });

  it('rolls back the package and the session when a chosen file cannot be carried', async () => {
    const source = await finishedSession(projectA.path, 'Rollback');
    const facts = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0];
    if (!facts) throw new Error('expected facts');
    // A link the agent could have left behind, pointing out of its own folder.
    symlinkSync(join(projectB.path), join(projectA.path, 'escape'));
    writeFileSync(join(projectB.path, 'secret.md'), 'not yours\n');
    const before = wrapupDirs();
    const lanesBefore = repo.listLanes(ctx.db, { includeArchived: true }).length;

    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: [{ laneId: source.lane.id, revision: facts.revision }],
        instructions: 'Use it.',
        files: [{ laneId: source.lane.id, path: 'escape/secret.md' }],
        acknowledgePartial: false,
      }),
    ).rejects.toMatchObject({ code: 'BAD_REQUEST', message: expect.stringContaining('cannot be read') });

    // Nothing had run in it, so the rollback destroys no work — and leaves nothing behind either.
    expect(wrapupDirs()).toEqual(before);
    expect(repo.listLanes(ctx.db, { includeArchived: true })).toHaveLength(lanesBefore);
  });

  it('refuses a file that is not text, by name, before it is opened', async () => {
    writeFileSync(join(projectA.path, 'drawing.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    const source = await finishedSession(projectA.path, 'Binary');
    const facts = (await caller.wrapups.sources({ laneIds: [source.lane.id] }))[0];
    if (!facts) throw new Error('expected facts');
    const before = wrapupDirs();
    await expect(
      caller.wrapups.create({
        projectRoot: projectA.path,
        sources: [{ laneId: source.lane.id, revision: facts.revision }],
        instructions: 'Use it.',
        files: [{ laneId: source.lane.id, path: 'drawing.png' }],
        acknowledgePartial: false,
      }),
    ).rejects.toMatchObject({ message: expect.stringContaining('not a text file') });
    expect(wrapupDirs()).toEqual(before);
  });
});

describe('wrapups.get', () => {
  it('answers null for an ordinary session', async () => {
    const plain = await caller.lanes.create({ cwd: projectA.path, name: 'Ordinary' });
    expect(await caller.wrapups.get({ laneId: plain.id })).toBeNull();
  });
});
