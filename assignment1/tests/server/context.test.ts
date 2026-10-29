import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { loadConfig } from '@/server/config';
import { createServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { makeTmpDir } from '../helpers/tmp';

const FAKE = fileURLToPath(new URL('../fake-claude/claude', import.meta.url));

describe('server context', () => {
  it('loads config from env with safe defaults', () => {
    const c = loadConfig(
      {
        MISSION_CONTROL_DATA_DIR: '/x/.data',
        MISSION_CONTROL_CLAUDE_BIN: FAKE,
        PORT: '4123',
      } as unknown as NodeJS.ProcessEnv,
      '/app',
    );
    expect(c).toMatchObject({
      host: '127.0.0.1',
      port: 4123,
      dataDir: '/x/.data',
      dbFile: '/x/.data/mission-control.db',
      claudeBin: FAKE,
    });
    expect(loadConfig({} as NodeJS.ProcessEnv, '/app').dataDir).toBe('/app/.data');
    expect(loadConfig({} as NodeJS.ProcessEnv, '/app').port).toBe(3000);
  });

  // R9: a relative override used to travel unresolved all the way to `git worktree add`, which
  // resolves it against the *target repository*, while the process manager resolved the same string
  // against the app's own cwd. Two different directories for one configured path. The data directory
  // is normalized once, here, against the app directory the server was started from.
  it('resolves a relative data directory against the app directory', () => {
    const c = loadConfig(
      { MISSION_CONTROL_DATA_DIR: 'relative-data' } as unknown as NodeJS.ProcessEnv,
      '/app',
    );
    expect(c.dataDir).toBe('/app/relative-data');
    expect(c.dbFile).toBe('/app/relative-data/mission-control.db');
    expect(
      loadConfig({ MISSION_CONTROL_DATA_DIR: './a/../b' } as unknown as NodeJS.ProcessEnv, '/app').dataDir,
    ).toBe('/app/b');
  });

  it('rejects a PORT that is not a usable port number', () => {
    for (const PORT of ['abc', '0', '-1', '65536', '3000.5']) {
      expect(() => loadConfig({ PORT } as unknown as NodeJS.ProcessEnv, '/app')).toThrow(
        /PORT must be an integer between 1 and 65535/,
      );
    }
    expect(loadConfig({ PORT: '65535' } as unknown as NodeJS.ProcessEnv, '/app').port).toBe(65535);
  });

  it('boots, migrates, and fails orphaned runs', async () => {
    const tmp = makeTmpDir();
    const config = loadConfig(
      {
        MISSION_CONTROL_DATA_DIR: tmp.path,
        MISSION_CONTROL_CLAUDE_BIN: FAKE,
      } as unknown as NodeJS.ProcessEnv,
      tmp.path,
    );
    const first = createServerContext(config);
    repo.createLane(first.db, { id: 'l', name: 'L', cwd: tmp.path, permission: 'allowlist', createdAt: 1 });
    repo.createRun(first.db, {
      id: 'orphan',
      laneId: 'l',
      prompt: 'p',
      effectiveCwd: tmp.path,
      permission: 'allowlist',
      origin: 'execution',
      status: 'running',
      startedAt: 1,
    });
    await first.close();
    const second = createServerContext(config);
    expect(repo.getRun(second.db, 'orphan')).toMatchObject({
      status: 'failed',
      errorMessage: 'server restarted',
    });
    await second.close();
    expect(join(tmp.path, 'mission-control.db')).toBe(config.dbFile);
    tmp.cleanup();
  });

  /**
   * Two signals arriving together used to close everything twice — the second `sqlite.close()`
   * landing on a connection the first had already closed, and whatever the first was still reaping
   * losing its owner part-way through.
   */
  it('closes once, however many times it is asked', async () => {
    const tmp = makeTmpDir();
    try {
      const ctx = createServerContext(
        loadConfig(
          {
            MISSION_CONTROL_DATA_DIR: tmp.path,
            MISSION_CONTROL_CLAUDE_BIN: FAKE,
          } as unknown as NodeJS.ProcessEnv,
          tmp.path,
        ),
      );
      const first = ctx.close();
      const second = ctx.close();
      expect(second).toBe(first);
      await Promise.all([first, second, ctx.close()]);
      // a fourth ask after it has already finished is still the same, settled, close
      await expect(ctx.close()).resolves.toEqual({ ok: true, unfinished: [] });
    } finally {
      tmp.cleanup();
    }
  });

  /**
   * Shutdown order. A paced replay is a chain of `setTimeout`s that write through the same SQLite
   * connection the context closes, so closing the connection first leaves timers armed against a
   * closed database. The context ends playbacks before it closes anything.
   */
  it('ends playbacks in flight before it closes the database', async () => {
    const tmp = makeTmpDir();
    try {
      const ctx = createServerContext(
        loadConfig(
          {
            MISSION_CONTROL_DATA_DIR: tmp.path,
            MISSION_CONTROL_CLAUDE_BIN: FAKE,
          } as unknown as NodeJS.ProcessEnv,
          tmp.path,
        ),
      );
      repo.createLane(ctx.db, {
        id: 'l',
        name: 'L',
        cwd: tmp.path,
        permission: 'allowlist',
        createdAt: 1,
      });
      repo.createRun(ctx.db, {
        id: 'source',
        laneId: 'l',
        prompt: 'p',
        effectiveCwd: tmp.path,
        permission: 'allowlist',
        origin: 'execution',
        status: 'finished',
        startedAt: 1000,
        endedAt: 4000,
      });
      for (let seq = 1; seq <= 4; seq += 1)
        repo.insertEvent(ctx.db, {
          runId: 'source',
          seq,
          receivedAt: 1000 + seq * 1000,
          type: 'system',
          parentToolUseId: null,
          json: JSON.stringify({ type: 'system', subtype: 'thinking_tokens' }),
        });
      const playing = ctx.replayer.start({ sourceRunId: 'source', speed: '1x' });
      expect(ctx.replayer.isLive(playing)).toBe(true);

      await ctx.close();

      expect(ctx.replayer.isLive(playing)).toBe(false);
      // whatever else happens, no timer may fire against the closed connection
      await new Promise((r) => setTimeout(r, 120));
      const reopened = createServerContext(
        loadConfig(
          {
            MISSION_CONTROL_DATA_DIR: tmp.path,
            MISSION_CONTROL_CLAUDE_BIN: FAKE,
          } as unknown as NodeJS.ProcessEnv,
          tmp.path,
        ),
      );
      // recorded as stopped, not left `running` for the next boot's orphan sweep to guess at
      expect(repo.getRun(reopened.db, playing)).toMatchObject({ status: 'cancelled', errorMessage: null });
      await reopened.close();
    } finally {
      tmp.cleanup();
    }
  });
});
