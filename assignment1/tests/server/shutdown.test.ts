import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { loadConfig } from '@/server/config';
import { type ContextSeams, createServerContext, type ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { createCaller } from '@/server/trpc/router';
import { makeTmpDir, makeTmpGitRepo } from '../helpers/tmp';

const alive = (pid: number) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

const FAKE_CLAUDE = fileURLToPath(new URL('../fake-claude/claude', import.meta.url));

/**
 * Shutdown ownership.
 *
 * The defect these cover: the server decided what it owned from the *responses* that were open, so
 * a browser that navigated away from a race it had just started — or a comparison whose HTTP
 * request had already been answered — left work running that shutdown believed was finished. It
 * then closed SQLite under that work, which reported it as `TypeError: The database connection is
 * not open`. Ownership now follows the work's own promise, comparisons are cancelled rather than
 * merely waited for, and a shutdown that cannot confirm cleanup says so instead of exiting quietly.
 */

/** A git that ignores nothing and simply takes `ms` to answer; cancellation reaches it normally. */
function slowGit(dir: string, ms: number): string {
  const bin = join(dir, 'slow-git');
  writeFileSync(bin, `#!/usr/bin/env node\nsetTimeout(() => process.exit(0), ${ms});\n`, { mode: 0o755 });
  return bin;
}

function contextIn(tmpPath: string, seams: ContextSeams): ServerContext {
  return createServerContext(
    loadConfig(
      {
        MISSION_CONTROL_DATA_DIR: tmpPath,
        MISSION_CONTROL_CLAUDE_BIN: FAKE_CLAUDE,
      } as unknown as NodeJS.ProcessEnv,
      tmpPath,
    ),
    seams,
  );
}

/** A context over a temp data directory, with one race whose lane has a worktree to diff. */
function contextWithRace(tmpPath: string, seams: ContextSeams): ServerContext {
  const ctx = contextIn(tmpPath, seams);
  repo.createGroup(ctx.db, { id: 'g', prompt: 'race', repoRoot: tmpPath, baseCommit: 'base', createdAt: 1 });
  repo.createLane(ctx.db, {
    id: 'l',
    name: 'Agent 1',
    cwd: tmpPath,
    permission: 'allowlist',
    createdAt: 1,
    groupId: 'g',
    groupIndex: 0,
  });
  repo.insertWorktree(ctx.db, {
    laneId: 'l',
    groupId: 'g',
    repoRoot: tmpPath,
    path: tmpPath,
    branch: 'mc/g/1',
    baseCommit: 'base',
  });
  repo.createRun(ctx.db, {
    id: 'r',
    laneId: 'l',
    groupId: 'g',
    prompt: 'race',
    effectiveCwd: tmpPath,
    permission: 'allowlist',
    origin: 'execution',
    status: 'finished',
    startedAt: 1,
    endedAt: 2,
  });
  return ctx;
}

describe('shutdown owns the work, not the response', () => {
  it('waits for request work whose client has already gone away', async () => {
    const tmp = makeTmpDir('mc-shutdown-abort-');
    let server: Server | undefined;
    let release = () => {};
    try {
      const ctx = contextWithRace(tmp.path, {});
      // `server.ts`'s wiring, in miniature: admission is a flag, and the work the request starts is
      // owned by `ctx.work` rather than by the response object.
      let closing = false;
      const blocked = new Promise<void>((resolve) => {
        release = resolve;
      });
      let workFinished = false;
      server = createServer((_req, res) => {
        if (closing) {
          res.writeHead(503);
          res.end('shutting down');
          return;
        }
        void ctx.work
          .track('fan-out test', async () => {
            await blocked;
            workFinished = true;
          })
          .then(() => {
            if (!res.destroyed) res.end('done');
          });
      });
      await new Promise<void>((r) => server?.listen(0, '127.0.0.1', () => r()));
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('no address');

      // The client asks, then vanishes — a closed tab, a reload, a dropped connection.
      const aborted = new AbortController();
      const inFlight = fetch(`http://127.0.0.1:${address.port}/fanout`, { signal: aborted.signal });
      await new Promise((r) => setTimeout(r, 50));
      aborted.abort();
      await inFlight.catch(() => undefined);
      await new Promise((r) => setTimeout(r, 50));

      // The response is gone; the work is not. This is exactly the point at which the old
      // socket-counting drain believed the server owned nothing.
      expect(ctx.work.pending()).toEqual(['fan-out test']);

      closing = true;
      let report: Awaited<ReturnType<typeof ctx.close>> | undefined;
      const shutdown = ctx.close().then((r) => {
        report = r;
        return r;
      });
      await new Promise((r) => setTimeout(r, 100));
      expect(report).toBeUndefined();
      expect(workFinished).toBe(false);
      // and the database it is still using is still open
      expect(ctx.sqlite.open).toBe(true);

      release();
      expect(await shutdown).toEqual({ ok: true, unfinished: [] });
      expect(workFinished).toBe(true);
      expect(ctx.sqlite.open).toBe(false);
    } finally {
      release();
      if (server) await new Promise<void>((r) => server?.close(() => r()));
      tmp.cleanup();
    }
  }, 20_000);

  it('refuses new request work once shutdown has begun', async () => {
    const tmp = makeTmpDir('mc-shutdown-admission-');
    try {
      const ctx = contextWithRace(tmp.path, {});
      await ctx.close();
      await expect(ctx.work.track('fan-out late', async () => 'started')).rejects.toThrow(/shutting down/);
      await expect(ctx.compare.compare('g')).rejects.toThrow(/shutting down/);
    } finally {
      tmp.cleanup();
    }
  });

  /**
   * Waiting is not enough for request work either. Preparing a race is several sequential git
   * commands, each with a 30-second budget of its own plus its cleanup, so a shutdown that only
   * waited would have to choose between a budget smaller than one command — abandoning a process it
   * knows how to stop — and a budget long enough to be useless. It cancels instead.
   */
  it('cancels a fan-out preparation that is still running, and reaps what it started', async () => {
    const repo = makeTmpGitRepo();
    const data = makeTmpDir('mc-shutdown-fanout-');
    const bin = makeTmpDir('mc-fake-git-');
    const pidFile = join(bin.path, 'filter.pid');
    const originalPath = process.env.PATH;
    let filter: number | undefined;
    try {
      // A `git worktree add` that never returns, having started a child of its own in its process
      // group — the shape of a repository whose hooks or filters hang. The child records its pid
      // only after installing its handlers, so the file's existence proves it is really ignoring
      // signals rather than having died before they were installed.
      const child = [
        "process.on('SIGINT', () => {});",
        "process.on('SIGTERM', () => {});",
        "require('node:fs').writeFileSync(process.argv[1], String(process.pid));",
        'setInterval(() => {}, 1000);',
        'setTimeout(() => process.exit(0), 20000);',
      ].join('');
      writeFileSync(
        join(bin.path, 'git'),
        [
          '#!/usr/bin/env node',
          "const { spawn } = require('node:child_process');",
          'const args = process.argv.slice(2);',
          // the questions asked before preparation starts still get real answers
          "if (args.includes('rev-parse')) { console.log(args.includes('HEAD') ? '0000000000000000000000000000000000000000' : 'true'); process.exit(0); }",
          "if (args.includes('worktree') && args.includes('add')) {",
          `  spawn(process.execPath, ['-e', ${JSON.stringify(child)}, ${JSON.stringify(pidFile)}], { stdio: 'ignore' });`,
          '  setInterval(() => {}, 1000);',
          '  setTimeout(() => process.exit(0), 20000);',
          '} else process.exit(0);',
        ].join('\n'),
        { mode: 0o755 },
      );
      process.env.PATH = `${bin.path}:${originalPath ?? ''}`;

      const ctx = contextIn(data.path, {});
      const caller = createCaller(ctx);
      const started = caller.fanout.start({ repoRoot: repo.path, prompt: 'race', laneCount: 2 });
      const refused = started.catch((err: unknown) => String(err));
      // wait until the hanging command's own child has proved itself
      for (let i = 0; i < 200 && !existsSync(pidFile); i += 1) await new Promise((r) => setTimeout(r, 25));
      expect(existsSync(pidFile)).toBe(true);
      filter = Number(readFileSync(pidFile, 'utf8'));
      expect(ctx.work.pending()).toHaveLength(1);

      const began = Date.now();
      const report = await ctx.close();
      const elapsed = Date.now() - began;

      // Settled, not abandoned: well inside one command's own 30 s budget.
      expect(report).toEqual({ ok: true, unfinished: [] });
      expect(elapsed).toBeLessThan(15_000);
      expect(await refused).toMatch(/shutting down|cancelled/);
      // the process the abandoned command started is gone too, not merely its leader
      expect(alive(filter)).toBe(false);
      // and nothing was spawned after the abort
      expect(ctx.sqlite.open).toBe(false);
    } finally {
      process.env.PATH = originalPath;
      if (filter !== undefined && alive(filter)) process.kill(filter, 'SIGKILL');
      bin.cleanup();
      data.cleanup();
      repo.cleanup();
    }
  }, 60_000);

  it('cancels a comparison in flight and closes the database only after it settles', async () => {
    const tmp = makeTmpDir('mc-shutdown-compare-');
    try {
      // Five seconds of git, cancelled in milliseconds: waiting this out is the behaviour being
      // ruled out, and a per-command timeout (30 s by default, and several commands per lane) would
      // not have bounded it either.
      const ctx = contextWithRace(tmp.path, { gitBin: slowGit(tmp.path, 5_000) });
      const pending = ctx.compare.compare('g');
      await new Promise((r) => setTimeout(r, 50));

      const started = Date.now();
      const report = await ctx.close();
      const elapsed = Date.now() - started;

      expect(report).toEqual({ ok: true, unfinished: [] });
      expect(elapsed).toBeLessThan(4_000);
      // The comparison settled *before* the database closed, so it never saw a closed connection.
      const result = await pending;
      expect(result.lanes[0]?.error).toMatch(/cancelled/);
      expect(ctx.sqlite.open).toBe(false);
    } finally {
      tmp.cleanup();
    }
  }, 30_000);

  /**
   * A8. An agent whose cleanup could not be confirmed is unfinished work, not a footnote.
   *
   * The manager used to warn about a surviving group, clear its ownership and resolve
   * `Promise<void>`, so `close()` had nothing to include and reported `ok: true` — a documented
   * contract ("every operation this server owned has settled") that the log on the line above
   * contradicted. It now names the run and the group, exits non-zero through `ok: false`, and
   * leaves the database open like any other unfinished work.
   *
   * `EPERM` from the liveness question is the unconfirmable OS boundary, simulated through the
   * process-control seam; real signals are still delivered, so nothing is left running.
   */
  it('reports an agent process group it could not confirm gone, and leaves the database open', async () => {
    const tmp = makeTmpDir('mc-shutdown-agent-');
    let pid: number | undefined;
    try {
      const ctx = contextIn(tmp.path, {
        processCancelGraceMs: 20,
        processControl: {
          kill(target, signal) {
            if (signal === 0) throw Object.assign(new Error('not permitted'), { code: 'EPERM' });
            process.kill(target, signal);
          },
        },
      });
      repo.createLane(ctx.db, {
        id: 'l',
        name: 'Agent',
        cwd: tmp.path,
        permission: 'allowlist',
        createdAt: 1,
      });
      await ctx.processes.start({
        runId: 'r',
        laneId: 'l',
        prompt: 'HANG',
        cwd: tmp.path,
        permission: 'allowlist',
      });
      pid = ctx.processes.pidOf('r');
      expect(pid).toBeGreaterThan(0);

      const report = await ctx.close();

      expect(report.ok).toBe(false);
      expect(report.unfinished).toEqual([`agent process group ${pid} of run r`]);
      // Same rule as unfinished request work: the connection stays open rather than failing
      // whatever is still using it with a spurious database fault.
      expect(ctx.sqlite.open).toBe(true);
      // The process itself really did go — only the confirmation was impossible.
      for (let i = 0; i < 200 && alive(pid as number); i += 1) await new Promise((r) => setTimeout(r, 25));
      expect(alive(pid as number)).toBe(false);
      ctx.sqlite.close();
    } finally {
      if (pid !== undefined && alive(pid)) process.kill(pid, 'SIGKILL');
      tmp.cleanup();
    }
  }, 30_000);

  it('confirms agent cleanup on the ordinary path and closes the database', async () => {
    const tmp = makeTmpDir('mc-shutdown-agent-ok-');
    try {
      const ctx = contextIn(tmp.path, { processCancelGraceMs: 100 });
      repo.createLane(ctx.db, {
        id: 'l',
        name: 'Agent',
        cwd: tmp.path,
        permission: 'allowlist',
        createdAt: 1,
      });
      await ctx.processes.start({
        runId: 'r',
        laneId: 'l',
        prompt: 'HANG',
        cwd: tmp.path,
        permission: 'allowlist',
      });
      const pid = ctx.processes.pidOf('r') as number;

      expect(ctx.runStatus('r')).toBe('running');

      expect(await ctx.close()).toEqual({ ok: true, unfinished: [] });
      expect(alive(pid)).toBe(false);
      expect(ctx.sqlite.open).toBe(false);
    } finally {
      tmp.cleanup();
    }
  }, 30_000);

  it('reports failure and leaves the database open when cleanup cannot be confirmed', async () => {
    const tmp = makeTmpDir('mc-shutdown-unconfirmed-');
    try {
      // The supervisor's reproduction, as a test: the real shutdown policy at a deliberately short
      // deadline. Cancelling a git command and confirming its process group is gone cannot happen
      // inside 5 ms, so shutdown must report that rather than assume it.
      const ctx = contextWithRace(tmp.path, {
        gitBin: slowGit(tmp.path, 2_000),
        shutdownBudgetMs: 5,
      });
      const pending = ctx.compare.compare('g');
      await new Promise((r) => setTimeout(r, 50));

      const report = await ctx.close();
      expect(report.ok).toBe(false);
      expect(report.unfinished).toEqual(['comparison g']);
      // Closing the connection here would replace "cleanup is unconfirmed" with a database fault in
      // work that is still running — which is exactly what the reproduction recorded.
      expect(ctx.sqlite.open).toBe(true);

      // That work then finishes on its own terms, reporting the cancellation rather than a closed
      // database, and it can still read the events it needs to finish its answer.
      const result = await pending;
      expect(result.lanes[0]?.error).toMatch(/cancelled/);
      expect(result.lanes[0]?.error ?? '').not.toMatch(/database connection is not open/);
      expect(ctx.sqlite.open).toBe(true);
      ctx.sqlite.close();
    } finally {
      tmp.cleanup();
    }
  }, 30_000);
});
