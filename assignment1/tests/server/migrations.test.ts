import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { describe, expect, it } from 'vitest';
import { openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { makeTmpDir } from '../helpers/tmp';

const DRIZZLE = join(process.cwd(), 'drizzle');

/** One migration's statements, in order, as the migrator would run them. */
function statements(file: string): string[] {
  return readFileSync(join(DRIZZLE, file), 'utf8')
    .split('--> statement-breakpoint')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

describe('0002_standalone_worktrees', () => {
  it('keeps every existing worktree and gives it the repository root of its race', () => {
    // A database as it stood before this migration: `group_id` NOT NULL, no `repo_root`.
    const sqlite = new Database(':memory:');
    sqlite.exec(`
      CREATE TABLE fanout_groups (
        id text PRIMARY KEY NOT NULL, prompt text NOT NULL, repo_root text NOT NULL,
        base_commit text NOT NULL, created_at integer NOT NULL, kept_run_id text
      );
      CREATE TABLE worktrees (
        lane_id text PRIMARY KEY NOT NULL, group_id text NOT NULL, path text NOT NULL,
        branch text NOT NULL, base_commit text NOT NULL
      );
      INSERT INTO fanout_groups VALUES ('g1', 'race it', '/repos/cafe', 'abc123', 1, NULL);
      INSERT INTO worktrees VALUES ('lane-a', 'g1', '/data/wt/0', 'mc/g1/0', 'abc123');
      INSERT INTO worktrees VALUES ('lane-b', 'g1', '/data/wt/1', 'mc/g1/1', 'abc123');
      -- A row whose race is gone: nothing in the old schema says what its repository was.
      INSERT INTO worktrees VALUES ('lane-orphan', 'g-missing', '/data/wt/x', 'mc/gx/0', 'def456');
    `);

    for (const sql of statements('0002_standalone_worktrees.sql')) sqlite.exec(sql);

    const rows = sqlite
      .prepare(
        'SELECT lane_id, group_id, repo_root, path, branch, base_commit FROM worktrees ORDER BY lane_id',
      )
      .all() as Record<string, unknown>[];
    // Nothing lost.
    expect(rows.map((r) => r.lane_id)).toEqual(['lane-a', 'lane-b', 'lane-orphan']);
    // Race membership and every other column survive untouched…
    expect(rows[0]).toMatchObject({
      lane_id: 'lane-a',
      group_id: 'g1',
      path: '/data/wt/0',
      branch: 'mc/g1/0',
      base_commit: 'abc123',
    });
    // …and the new column is backfilled from the race that created the worktree.
    expect(rows[0]?.repo_root).toBe('/repos/cafe');
    expect(rows[1]?.repo_root).toBe('/repos/cafe');
    // The orphan keeps a NULL rather than a made-up path: the old schema does not know.
    expect(rows[2]?.repo_root).toBeNull();

    // And the constraint is actually relaxed — a worktree with no race is now representable.
    sqlite
      .prepare('INSERT INTO worktrees VALUES (?, ?, ?, ?, ?, ?)')
      .run('lane-solo', null, '/repos/cafe', '/data/wt/session', 'mc/session/lane-solo', 'abc123');
    const solo = sqlite.prepare('SELECT * FROM worktrees WHERE lane_id = ?').get('lane-solo') as Record<
      string,
      unknown
    >;
    expect(solo.group_id).toBeNull();
    sqlite.close();
  });

  it('lets `worktreeRepoRoot` fall back to the race when a row predates the column', () => {
    const dir = makeTmpDir('mc-db-');
    try {
      const { db } = openDb(join(dir.path, 'mc.db'));
      repo.createGroup(db, {
        id: 'g1',
        prompt: 'race',
        repoRoot: '/repos/cafe',
        baseCommit: 'abc',
        createdAt: 1,
      });
      // As a pre-0002 row looks after the backfill could not run (a partially restored database).
      repo.insertWorktree(db, {
        laneId: 'lane-a',
        groupId: 'g1',
        repoRoot: null,
        path: '/data/wt/0',
        branch: 'mc/g1/0',
        baseCommit: 'abc',
      });
      expect(repo.worktreeRepoRoot(db, 'lane-a')).toBe('/repos/cafe');
      // No worktree at all is `null`, not a throw: most lanes have none.
      expect(repo.worktreeRepoRoot(db, 'lane-none')).toBeNull();
    } finally {
      dir.cleanup();
    }
  });
});

describe('0003_browser_mode', () => {
  it("keeps each run's own recorded browser request, which is what replay reads", () => {
    const dir = makeTmpDir('mc-db-');
    try {
      const { db, sqlite } = openDb(join(dir.path, 'mc.db'));
      repo.createLane(db, { id: 'l1', name: 'L', cwd: '/tmp', permission: 'allowlist', createdAt: 1 });
      // The default is `off`, which is all an old row can mean: nothing in the old console passed
      // a Chrome flag. New executions record `chrome` because that is what they are launched with.
      const old = repo.createRun(db, {
        id: 'r1',
        laneId: 'l1',
        prompt: 'p',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        origin: 'execution',
        status: 'finished',
        startedAt: 1,
      });
      expect(old.browser).toBe('off');
      const now = repo.createRun(db, {
        id: 'r2',
        laneId: 'l1',
        prompt: 'p',
        effectiveCwd: '/tmp',
        permission: 'allowlist',
        browser: 'chrome',
        origin: 'execution',
        status: 'running',
        startedAt: 2,
      });
      expect(now.browser).toBe('chrome');
      // A closed domain in the database as well as in the type.
      expect(() => sqlite.prepare('UPDATE runs SET browser = ? WHERE id = ?').run('nope', 'r1')).toThrow(
        /CHECK/i,
      );
      sqlite.close();
    } finally {
      dir.cleanup();
    }
  });
});

/**
 * Project scope, archiving, and the end of the per-session browser switch.
 *
 * The migration has to be run against a database written by the *previous* schema, so this builds
 * one with raw SQL — the current Drizzle schema no longer has the columns being migrated away
 * from, and creating rows through `repo` would not exercise the backfill at all.
 */
describe('0004_project_scope', () => {
  /** The four migrations that existed before this one, as their own folder. */
  function applyMigrationsUpTo0003(file: string, tmp: string): Database.Database {
    const folder = join(tmp, 'legacy-migrations');
    mkdirSync(join(folder, 'meta'), { recursive: true });
    const journal = JSON.parse(readFileSync(join(DRIZZLE, 'meta', '_journal.json'), 'utf8')) as {
      entries: { tag: string }[];
    };
    const kept = journal.entries.filter((e) => e.tag < '0004_project_scope');
    for (const e of kept) copyFileSync(join(DRIZZLE, `${e.tag}.sql`), join(folder, `${e.tag}.sql`));
    writeFileSync(join(folder, 'meta', '_journal.json'), JSON.stringify({ ...journal, entries: kept }));
    const sqlite = new Database(file);
    migrate(drizzle(sqlite), { migrationsFolder: folder });
    return sqlite;
  }

  it('gives every session a project: the managed checkout’s repository, else its own folder', () => {
    const dir = makeTmpDir('mc-db-');
    try {
      const file = join(dir.path, 'mc.db');
      const legacy = applyMigrationsUpTo0003(file, dir.path);
      legacy
        .prepare(
          'INSERT INTO lanes (id, name, cwd, permission, created_at, closed_at, browser) VALUES (?,?,?,?,?,?,?)',
        )
        .run('plain', 'Plain', '/projects/notes', 'allowlist', 1, null, 'off');
      legacy
        .prepare(
          'INSERT INTO lanes (id, name, cwd, permission, created_at, closed_at, browser) VALUES (?,?,?,?,?,?,?)',
        )
        .run('isolated', 'Isolated', '/data/worktrees/session/isolated', 'allowlist', 2, 5, 'chrome');
      legacy
        .prepare(
          'INSERT INTO worktrees (lane_id, group_id, repo_root, path, branch, base_commit) VALUES (?,?,?,?,?,?)',
        )
        .run('isolated', null, '/projects/notes', '/data/worktrees/session/isolated', 'mc/session/x', 'abc');
      legacy.close();

      const { db, sqlite } = openDb(file);
      const lanes = repo.listLanes(db, { includeArchived: true });
      // The isolated session's *files* are in the data directory; its project is the repository it
      // was branched from, so the two sessions list together.
      expect(lanes.map((l) => [l.id, l.projectRoot])).toEqual([
        ['plain', '/projects/notes'],
        ['isolated', '/projects/notes'],
      ]);
      // `closed_at` became `archived_at` — the same fact, reversibly named.
      expect(lanes.find((l) => l.id === 'plain')?.archivedAt).toBeNull();
      expect(lanes.find((l) => l.id === 'isolated')?.archivedAt).toBe(5);
      // The per-session switch is gone; nothing can set it any more.
      expect(
        sqlite
          .prepare('PRAGMA table_info(lanes)')
          .all()
          .map((c) => (c as { name: string }).name),
      ).not.toContain('browser');
      sqlite.close();
    } finally {
      dir.cleanup();
    }
  });
});

/**
 * Wrap-ups are additive, and the journal is ordered: a migration stamped before one already applied
 * is skipped by the migrator, which is how these four tables silently failed to appear on an
 * existing database. This upgrades a database created by the previous schema and checks they do.
 */
describe('0005_wrapups', () => {
  it('appears on an upgrade from an existing database, and leaves its history alone', () => {
    const dir = makeTmpDir('mc-db-');
    try {
      const file = join(dir.path, 'mc.db');
      const folder = join(dir.path, 'pre-0005');
      mkdirSync(join(folder, 'meta'), { recursive: true });
      const journal = JSON.parse(readFileSync(join(DRIZZLE, 'meta', '_journal.json'), 'utf8')) as {
        entries: { tag: string; when: number }[];
      };
      const kept = journal.entries.filter((e) => e.tag < '0005_wrapups');
      for (const e of kept) copyFileSync(join(DRIZZLE, `${e.tag}.sql`), join(folder, `${e.tag}.sql`));
      writeFileSync(join(folder, 'meta', '_journal.json'), JSON.stringify({ ...journal, entries: kept }));
      // The journal is ordered by `when`, and the migrator applies only what is newer than the last
      // one it ran. A new migration stamped earlier than an applied one never runs.
      const last = Math.max(...kept.map((e) => e.when));
      const added = journal.entries.find((e) => e.tag === '0005_wrapups');
      expect(added?.when).toBeGreaterThan(last);

      const legacy = new Database(file);
      migrate(drizzle(legacy), { migrationsFolder: folder });
      legacy
        .prepare(
          'INSERT INTO lanes (id, name, cwd, project_root, permission, created_at) VALUES (?,?,?,?,?,?)',
        )
        .run('before', 'Before', '/projects/notes', '/projects/notes', 'allowlist', 1);
      legacy.close();

      const { db, sqlite } = openDb(file);
      // The upgrade runs 0005 and the tables are there…
      const tables = sqlite
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
        .all()
        .map((t) => (t as { name: string }).name);
      for (const t of ['wrapups', 'wrapup_sources', 'wrapup_runs', 'wrapup_files'])
        expect(tables).toContain(t);
      // …the session that predates them is untouched, and simply has no wrap-up.
      expect(repo.listLanes(db).map((l) => l.id)).toEqual(['before']);
      expect(repo.getWrapUp(db, 'before')).toBeUndefined();
      sqlite.close();
    } finally {
      dir.cleanup();
    }
  });
});
