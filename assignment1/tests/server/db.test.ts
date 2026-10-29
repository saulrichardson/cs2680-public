import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { afterEach, describe, expect, it } from 'vitest';
import { openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import type { RunRow } from '@/server/db/schema';
import { makeTmpDir } from '../helpers/tmp';

describe('database', () => {
  const tmp = makeTmpDir();
  afterEach(() => tmp.cleanup());

  it('migrates a fresh file and round-trips lanes, runs, events', () => {
    const { db, sqlite } = openDb(join(tmp.path, 'test.db'));
    const lane = repo.createLane(db, {
      id: 'lane1',
      name: 'Lane 1',
      cwd: '/tmp/x',
      permission: 'allowlist',
      createdAt: 1,
    });
    expect(repo.getLane(db, 'lane1')).toEqual(lane);
    expect(repo.listLanes(db).map((l) => l.id)).toEqual(['lane1']);

    const run = repo.createRun(db, {
      id: 'run1',
      laneId: 'lane1',
      prompt: 'hi',
      effectiveCwd: '/tmp/x',
      permission: 'allowlist',
      origin: 'execution',
      status: 'running',
      startedAt: 10,
    });
    expect(repo.getRun(db, 'run1')?.status).toBe('running');

    repo.insertEvent(db, {
      runId: 'run1',
      seq: 1,
      receivedAt: 11,
      type: 'system',
      parentToolUseId: null,
      json: '{"type":"system","subtype":"init"}',
    });
    repo.insertEvent(db, {
      runId: 'run1',
      seq: 2,
      receivedAt: 12,
      type: 'assistant',
      parentToolUseId: null,
      json: '{"type":"assistant"}',
    });
    expect(repo.listEvents(db, 'run1').map((e) => e.seq)).toEqual([1, 2]);
    expect(repo.listEvents(db, 'run1', 1).map((e) => e.seq)).toEqual([2]);

    repo.updateRun(db, 'run1', {
      status: 'finished',
      endedAt: 20,
      sessionId: 'sess',
      costUsd: 0.5,
      durationMs: 10,
      numTurns: 2,
      exitCode: 0,
    });
    expect(repo.getRun(db, 'run1')).toMatchObject({ status: 'finished', sessionId: 'sess', costUsd: 0.5 });
    expect(repo.latestSessionId(db, 'lane1')).toBe('sess');
    expect(run.id).toBe('run1');
    sqlite.close();
  });

  it('only offers runs this lane really executed as resume candidates', () => {
    const { db, sqlite } = openDb(':memory:');
    repo.createLane(db, { id: 'l', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
    const run = (id: string, over: Partial<repo.NewRun> & { sessionId?: string; numTurns?: number }) => {
      const { sessionId, numTurns, ...rest } = over;
      repo.createRun(db, {
        id,
        laneId: 'l',
        prompt: id,
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        origin: 'execution',
        status: 'finished',
        startedAt: 10,
        ...rest,
      });
      repo.updateRun(db, id, { sessionId: sessionId ?? null, numTurns: numTurns ?? null });
      return repo.getRun(db, id) as NonNullable<ReturnType<typeof repo.getRun>>;
    };

    // the run the lane actually executed, three runs that only look like it, and a live one.
    // A replay and an import are told apart by what they are (`origin`), not by the side effects
    // that used to stand in for it — a replay now keeps its session id and is still refused.
    const real = run('real', { sessionId: 'sess-real', numTurns: 4, startedAt: 10 });
    const imported = run('imported', {
      origin: 'import',
      prompt: 'Imported: events.jsonl',
      startedAt: 20,
    });
    const replayed = run('replayed', {
      origin: 'replay',
      sessionId: 'sess-real',
      numTurns: 4,
      replayOf: 'real',
      startedAt: 30,
    });
    const zeroTurns = run('zero', { sessionId: 'sess-fresh', numTurns: 0, status: 'failed', startedAt: 40 });
    const live = run('live', { sessionId: 'sess-live', status: 'running', startedAt: 50 }); // turns unknown

    expect(repo.isResumeCandidate(real)).toBe(true);
    expect(repo.isResumeCandidate(imported)).toBe(false);
    expect(repo.isResumeCandidate(replayed)).toBe(false);
    expect(repo.isResumeCandidate(zeroTurns)).toBe(false);
    expect(repo.isResumeCandidate(live)).toBe(true);
    expect(repo.lifecycleOf(zeroTurns).resumable).toBe(false);

    // newest candidate wins, and the three non-candidates above it are skipped
    expect(repo.latestSessionId(db, 'l')).toBe('sess-live');
    repo.updateRun(db, 'live', { status: 'cancelled', numTurns: 7 });
    expect(repo.latestSessionId(db, 'l')).toBe('sess-live'); // a cancelled run with turns still counts
    repo.updateRun(db, 'live', { sessionId: null });
    expect(repo.latestSessionId(db, 'l')).toBe('sess-real');
    repo.updateRun(db, 'real', { numTurns: 0 });
    expect(repo.latestSessionId(db, 'l')).toBeUndefined();
    sqlite.close();
  });

  it('marks orphaned running runs failed on boot', () => {
    const { db, sqlite } = openDb(':memory:');
    repo.createLane(db, { id: 'l', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
    repo.createRun(db, {
      id: 'r1',
      laneId: 'l',
      prompt: 'a',
      effectiveCwd: '/tmp',
      permission: 'allowlist',
      origin: 'execution',
      status: 'running',
      startedAt: 1,
    });
    repo.createRun(db, {
      id: 'r2',
      laneId: 'l',
      prompt: 'b',
      effectiveCwd: '/tmp',
      permission: 'allowlist',
      origin: 'execution',
      status: 'finished',
      startedAt: 2,
      endedAt: 3,
    });
    const orphans = repo.failOrphanRuns(db, 100, 'server restarted');
    expect(orphans.map((r) => r.id)).toEqual(['r1']);
    expect(repo.getRun(db, 'r1')).toMatchObject({
      status: 'failed',
      errorMessage: 'server restarted',
      endedAt: 100,
    });
    expect(repo.getRun(db, 'r2')?.status).toBe('finished');
    sqlite.close();
  });

  it('round-trips a malformed stored line as text and reads it back as an unparsed event', () => {
    const { db, sqlite } = openDb(':memory:');
    repo.createLane(db, { id: 'l', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
    repo.createRun(db, {
      id: 'r',
      laneId: 'l',
      prompt: 'p',
      effectiveCwd: '/tmp',
      permission: 'allowlist',
      origin: 'execution',
      status: 'finished',
      startedAt: 1,
      endedAt: 2,
    });
    const lines = ['{"type":"system","subtype":"init"}', '{this is not json', '{"type":"result"}'];
    lines.forEach((json, i) => {
      repo.insertEvent(db, {
        runId: 'r',
        seq: i + 1,
        receivedAt: i + 1,
        type: i === 1 ? 'unparsed' : 'system',
        parentToolUseId: null,
        json,
      });
    });
    const rows = repo.listEvents(db, 'r');
    // exactly how `GET /api/export` builds its body: the agent's own text, byte for byte
    expect(rows.map((e) => e.json).join('\n')).toBe(lines.join('\n'));
    // ... while every reader still gets a usable event for the malformed line
    expect(rows.map((r) => repo.eventOf(r).type)).toEqual(['system', 'unparsed', 'result']);
    expect(repo.toEnvelope(rows[1] as (typeof rows)[number], 'l')).toMatchObject({
      laneId: 'l',
      seq: 2,
      event: { type: 'unparsed', raw: '{this is not json' },
    });
    sqlite.close();
  });

  it('stores groups and worktrees', () => {
    const { db, sqlite } = openDb(':memory:');
    repo.createGroup(db, { id: 'g', prompt: 'p', repoRoot: '/r', baseCommit: 'abc', createdAt: 1 });
    repo.createLane(db, {
      id: 'l0',
      name: 'Agent 1',
      cwd: '/w/0',
      permission: 'allowlist',
      createdAt: 1,
      groupId: 'g',
      groupIndex: 0,
    });
    repo.insertWorktree(db, {
      laneId: 'l0',
      groupId: 'g',
      repoRoot: '/repo',
      path: '/w/0',
      branch: 'mc/g/0',
      baseCommit: 'abc',
    });
    expect(repo.getGroup(db, 'g')?.baseCommit).toBe('abc');
    expect(repo.listGroupLanes(db, 'g').map((l) => l.id)).toEqual(['l0']);
    expect(repo.getWorktree(db, 'l0')?.branch).toBe('mc/g/0');
    repo.setKeptRun(db, 'g', 'run-x');
    expect(repo.getGroup(db, 'g')?.keptRunId).toBe('run-x');
    sqlite.close();
  });
});

/**
 * The `origin` column is new, so every run recorded before it exists has to be classified from what
 * the old schema did record. This exercises the real migration against a real database built at the
 * previous schema version — not a hand-written approximation of one — because getting the backfill
 * wrong would silently take historical executions out of races and offer imported sessions to
 * `--resume`.
 *
 * The fixture is built by running migration 0000 and then telling drizzle's migrator it has already
 * been applied, which is exactly the state a database created by the previous build is in.
 */
function legacyDatabaseAtV0(file: string): void {
  const sqlite = new Database(file);
  const migrationsDir = fileURLToPath(new URL('../../drizzle', import.meta.url));
  const journal = JSON.parse(readFileSync(join(migrationsDir, 'meta', '_journal.json'), 'utf8')) as {
    entries: { tag: string; when: number }[];
  };
  const first = journal.entries[0];
  if (!first) throw new Error('no migrations to build a legacy database from');
  for (const statement of readFileSync(join(migrationsDir, `${first.tag}.sql`), 'utf8').split(
    '--> statement-breakpoint',
  ))
    sqlite.exec(statement);
  sqlite.exec(
    'CREATE TABLE IF NOT EXISTS `__drizzle_migrations` (id SERIAL PRIMARY KEY, hash text NOT NULL, created_at numeric)',
  );
  sqlite
    .prepare('INSERT INTO `__drizzle_migrations` (hash, created_at) VALUES (?, ?)')
    .run(first.tag, first.when);
  sqlite.close();
}

describe('migrating a database recorded before runs had an origin', () => {
  const tmp = makeTmpDir('mc-legacy-');
  afterEach(() => tmp.cleanup());

  it('classifies legacy rows as execution, replay, or import', () => {
    const file = join(tmp.path, 'legacy.db');
    legacyDatabaseAtV0(file);

    // Insert through raw SQL: the old rows had no `origin` column to set.
    const legacy = new Database(file);
    expect(legacy.prepare("SELECT * FROM pragma_table_info('runs') WHERE name = ?").get('origin')).toBe(
      undefined,
    );
    legacy.exec(
      "INSERT INTO lanes (id, name, cwd, permission, created_at) VALUES ('l', 'L', '/tmp', 'allowlist', 1)",
    );
    const insert = legacy.prepare(
      `INSERT INTO runs (id, lane_id, group_id, prompt, effective_cwd, permission, status, started_at, session_id, resumed_from, replay_of, num_turns, exit_code, signal, stderr_tail, error_message)
       VALUES (@id, 'l', @groupId, @prompt, '/tmp', @permission, @status, @startedAt, @sessionId, @resumedFrom, @replayOf, @numTurns, @exitCode, @signal, @stderrTail, @errorMessage)`,
    );
    const row = (over: Record<string, unknown>) =>
      insert.run({
        prompt: 'do the thing',
        status: 'finished',
        startedAt: 1,
        groupId: null,
        permission: 'allowlist',
        sessionId: null,
        resumedFrom: null,
        replayOf: null,
        numTurns: 3,
        exitCode: 0,
        signal: null,
        stderrTail: null,
        errorMessage: null,
        ...over,
      });
    row({ id: 'executed', sessionId: 'sess-1' });
    row({ id: 'replayed', replayOf: 'executed', sessionId: 'sess-1', exitCode: null });
    row({ id: 'imported', prompt: 'Imported: events.jsonl', sessionId: null, exitCode: null });
    // A real row from the runtime database: an older `Replayer.import` stored the recording's own
    // session id. Recognising an import by "no session" alone would read this as an execution and
    // offer `s-import-1` to `--resume`, which is the exact CLI failure `isResumeCandidate` exists
    // to prevent.
    row({
      id: 'imported-with-session',
      prompt: 'Imported: events.jsonl',
      sessionId: 's-import-1',
      exitCode: null,
    });
    // The case the backfill must not get wrong in the other direction: an execution that failed
    // before its init event, so it never recorded a session. It spawned an agent in a directory, and
    // came back through `close` with an exit code — so it is still an execution.
    row({
      id: 'failed-early',
      status: 'failed',
      sessionId: null,
      numTurns: null,
      exitCode: 1,
      prompt: 'fix the bug',
    });
    // ... and one an operator stopped, killed by a signal rather than exiting
    row({
      id: 'killed',
      status: 'cancelled',
      sessionId: 'sess-2',
      exitCode: null,
      signal: 'SIGKILL',
      prompt: 'Imported: something the operator typed',
    });
    // ... and a run against a bad directory, which never spawned at all
    row({
      id: 'bad-directory',
      status: 'failed',
      sessionId: null,
      numTurns: null,
      exitCode: null,
      prompt: 'anything',
    });
    // The counterexamples. Each of these is a real execution whose process columns are all null —
    // the claim that "an execution that spawned is never caught by this rule" is false, so the rule
    // leans on what the import writer never did instead of on what the manager always does.
    //
    // A run interrupted by a server crash: it emitted events and a session, but never reached the
    // manager's close handler. Before the next boot it is still `running`.
    row({
      id: 'crashed-mid-run',
      status: 'running',
      sessionId: 'sess-crash',
      numTurns: 2,
      exitCode: null,
      prompt: 'Imported: my notes about the importer',
    });
    // ... and after it, `failOrphanRuns` has written exactly this message.
    row({
      id: 'crashed-and-swept',
      status: 'failed',
      sessionId: 'sess-swept',
      numTurns: 2,
      exitCode: null,
      errorMessage: 'server restarted',
      prompt: 'Imported: my notes about the importer',
    });
    // A failed race candidate whose prompt happens to start that way. Only a fan-out sets group_id.
    row({
      id: 'race-candidate',
      groupId: 'some-race',
      status: 'failed',
      sessionId: null,
      numTurns: null,
      exitCode: null,
      errorMessage: 'the agent exited without a result event',
      prompt: 'Imported: the CSV loader, then fix the failing test',
    });
    // A follow-up. Only a resume sets resumed_from.
    row({
      id: 'follow-up',
      resumedFrom: 'executed',
      sessionId: 'sess-1',
      exitCode: null,
      prompt: 'Imported: keep going from there',
    });
    // A lane running with permission checks skipped. The import writer always wrote 'allowlist'.
    row({
      id: 'bypass-run',
      permission: 'bypass',
      sessionId: null,
      numTurns: null,
      exitCode: null,
      prompt: 'Imported: whatever the operator typed',
    });
    // Case matters: SQLite's LIKE would have matched this, `substr(...) =` does not.
    row({
      id: 'lowercase-prompt',
      sessionId: null,
      numTurns: null,
      exitCode: null,
      prompt: 'imported: a lowercase prompt',
    });
    legacy.close();

    const { db, sqlite } = openDb(file);
    const run = (id: string): RunRow => {
      const found = repo.getRun(db, id);
      if (!found) throw new Error(`the migration lost run ${id}`);
      return found;
    };
    expect(run('executed').origin).toBe('execution');
    expect(run('replayed').origin).toBe('replay');
    expect(run('imported').origin).toBe('import');
    expect(run('imported-with-session').origin).toBe('import');
    expect(run('failed-early').origin).toBe('execution');
    // A run a signal killed had a process, whatever its prompt says.
    expect(run('killed').origin).toBe('execution');
    // The counterexamples: every one of these is an execution, and each is excluded by a different
    // invariant of the old import writer rather than by the process columns.
    expect(run('crashed-mid-run').origin).toBe('execution');
    expect(run('crashed-and-swept').origin).toBe('execution');
    expect(run('race-candidate').origin).toBe('execution');
    expect(run('follow-up').origin).toBe('execution');
    expect(run('bypass-run').origin).toBe('execution');
    expect(run('lowercase-prompt').origin).toBe('execution');
    // ... and the race candidate is therefore still part of its race's history
    expect(repo.listExecutions(db, 'l').map((r) => r.id)).toContain('race-candidate');

    // and the classification is what the resume rule and the candidate history now read
    expect(repo.isResumeCandidate(run('executed'))).toBe(true);
    expect(repo.isResumeCandidate(run('replayed'))).toBe(false);
    expect(repo.isResumeCandidate(run('imported-with-session'))).toBe(false);
    expect(repo.latestSessionId(db, 'l')).not.toBe('s-import-1');
    expect(
      repo
        .listExecutions(db, 'l')
        .map((r) => r.id)
        .sort(),
    ).toEqual([
      'bad-directory',
      'bypass-run',
      'crashed-and-swept',
      'crashed-mid-run',
      'executed',
      'failed-early',
      'follow-up',
      'killed',
      'lowercase-prompt',
      'race-candidate',
    ]);
    // every other column survived untouched
    expect(run('executed')).toMatchObject({ prompt: 'do the thing', sessionId: 'sess-1', numTurns: 3 });
    sqlite.close();

    // re-opening applies nothing further and changes nothing
    const again = openDb(file);
    expect((repo.getRun(again.db, 'replayed') as RunRow).origin).toBe('replay');
    expect(repo.listRuns(again.db, 'l')).toHaveLength(13);
    again.sqlite.close();
  });
});
