import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createServerContext, type ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import {
  describeCheckout,
  git,
  prepareStandaloneWorktree,
  prepareWorktrees,
  resolveRepoRoot,
} from '@/server/git/worktrees';
import { createCaller } from '@/server/trpc/router';
import { makeTmpDir, makeTmpGitRepo } from '../helpers/tmp';

function ctxIn(dataDir: string): ServerContext {
  return createServerContext({
    host: '127.0.0.1',
    port: 0,
    dataDir,
    dbFile: ':memory:',
    claudeBin: '/bin/false',
    scratchRoot: join(dataDir, 'scratch'),
  });
}

/** A context whose agents never actually spawn: these tests are about checkouts, not runs. */
async function harness() {
  const source = makeTmpGitRepo();
  const data = makeTmpDir('mc-data-');
  const ctx = ctxIn(data.path);
  return {
    source,
    data,
    ctx,
    caller: createCaller(ctx),
    cleanup: async () => {
      await ctx.close();
      data.cleanup();
      source.cleanup();
    },
  };
}

const sh = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8' }).trim();

describe('independent worktree sessions', () => {
  it('creates a session in its own branch and directory, from the source checkout’s HEAD', async () => {
    const h = await harness();
    try {
      const first = await h.caller.lanes.create({ cwd: h.source.path, name: 'Cafe' });
      const made = await h.caller.workspaces.create({ fromLaneId: first.id, name: 'Plumbing' });

      expect(made.baseCommit).toBe(h.source.head);
      expect(made.repoRoot).toBe(sh(h.source.path, 'rev-parse', '--show-toplevel'));
      // A different directory and a different branch: that is what makes the two independent.
      expect(made.lane.cwd).not.toBe(h.source.path);
      expect(made.lane.cwd).toBe(made.path);
      expect(made.branch).toBe(`mc/session/${made.lane.id}`);
      expect(sh(made.path, 'rev-parse', 'HEAD')).toBe(h.source.head);
      expect(sh(made.path, 'rev-parse', '--abbrev-ref', 'HEAD')).toBe(made.branch);
      expect(sh(h.source.path, 'rev-parse', '--abbrev-ref', 'HEAD')).toBe('main');
      // The committed content really is there — which is the whole point of a handoff.
      expect(readFileSync(join(made.path, 'README.md'), 'utf8')).toBe('# demo\n');
      expect(existsSync(join(made.path, 'src', 'app.py'))).toBe(true);
    } finally {
      await h.cleanup();
    }
  });

  it('is not a race: no fan-out group, no group id, nothing for Compare to list', async () => {
    const h = await harness();
    try {
      const first = await h.caller.lanes.create({ cwd: h.source.path });
      const made = await h.caller.workspaces.create({ fromLaneId: first.id });
      expect(await h.caller.fanout.list()).toEqual([]);
      expect(made.lane.groupId).toBeNull();
      expect(made.lane.groupIndex).toBeNull();
      const row = repo.getWorktree(h.ctx.db, made.lane.id);
      expect(row?.groupId).toBeNull();
      expect(row?.repoRoot).toBe(made.repoRoot);
      // And the durable repo root is readable without a group to borrow it from.
      expect(repo.worktreeRepoRoot(h.ctx.db, made.lane.id)).toBe(made.repoRoot);
    } finally {
      await h.cleanup();
    }
  });

  it('leaves uncommitted work in the source checkout, and says how much stayed behind', async () => {
    const h = await harness();
    try {
      // The case the dialog warns about: a handoff written but not committed.
      writeFileSync(join(h.source.path, 'README.md'), '# demo\n\nedited but not committed\n');
      writeFileSync(join(h.source.path, 'HANDOFF.md'), 'untracked\n');
      const first = await h.caller.lanes.create({ cwd: h.source.path });

      const preview = await h.caller.workspaces.preview({ fromLaneId: first.id });
      expect(preview.isGitRepo).toBe(true);
      if (!preview.isGitRepo) throw new Error('expected a git repository');
      expect(preview.dirtyFiles).toBe(2);
      expect(preview.baseCommit).toBe(h.source.head);

      const made = await h.caller.workspaces.create({ fromLaneId: first.id });
      // Neither carried across — no silent copy of a partial edit…
      expect(readFileSync(join(made.path, 'README.md'), 'utf8')).toBe('# demo\n');
      expect(existsSync(join(made.path, 'HANDOFF.md'))).toBe(false);
      // …and, just as important, nothing was committed on the operator's behalf, so their working
      // copy is exactly as they left it.
      expect(readFileSync(join(h.source.path, 'README.md'), 'utf8')).toContain('not committed');
      expect(existsSync(join(h.source.path, 'HANDOFF.md'))).toBe(true);
      expect(sh(h.source.path, 'status', '--porcelain').split('\n')).toHaveLength(2);
    } finally {
      await h.cleanup();
    }
  });

  it('carries a committed handoff into the new checkout', async () => {
    const h = await harness();
    try {
      // What the cafe parent is asked to do: save the package and commit it.
      writeFileSync(join(h.source.path, 'HANDOFF-plumbing.md'), '# Plumbing brief\n\nTwo sinks.\n');
      sh(h.source.path, 'add', '-A');
      sh(h.source.path, 'commit', '-q', '-m', 'handoff');
      const head = sh(h.source.path, 'rev-parse', 'HEAD');

      const first = await h.caller.lanes.create({ cwd: h.source.path });
      const made = await h.caller.workspaces.create({ fromLaneId: first.id });
      expect(made.baseCommit).toBe(head);
      expect(readFileSync(join(made.path, 'HANDOFF-plumbing.md'), 'utf8')).toContain('Two sinks');
    } finally {
      await h.cleanup();
    }
  });

  it('branches from the *source* checkout’s HEAD, not the main working tree’s', async () => {
    // The review's sharpest case: creating a session from a lane that is itself a linked worktree
    // whose branch has moved on. Taking the main checkout's HEAD would silently drop those commits.
    const h = await harness();
    try {
      const { worktrees } = await prepareWorktrees({
        repoRoot: h.source.path,
        groupId: 'g1',
        count: 2,
        dataDir: h.data.path,
      });
      const lane = worktrees[0];
      if (!lane) throw new Error('expected a prepared worktree');
      writeFileSync(join(lane.path, 'LANE.md'), 'work done in the lane\n');
      sh(lane.path, 'add', '-A');
      sh(lane.path, 'commit', '-q', '-m', 'lane work');
      const laneHead = sh(lane.path, 'rev-parse', 'HEAD');
      expect(laneHead).not.toBe(h.source.head);

      const racing = await h.caller.lanes.create({ cwd: lane.path, name: 'Agent 1' });
      const made = await h.caller.workspaces.create({ fromLaneId: racing.id });

      expect(made.baseCommit).toBe(laneHead);
      expect(readFileSync(join(made.path, 'LANE.md'), 'utf8')).toBe('work done in the lane\n');
      // The repository identity is still the one main working tree, whichever checkout we came from.
      expect(made.repoRoot).toBe(sh(h.source.path, 'rev-parse', '--show-toplevel'));
    } finally {
      await h.cleanup();
    }
  });

  it('refuses a directory that is not a git repository, and says what to do instead', async () => {
    const h = await harness();
    const plain = makeTmpDir('mc-plain-');
    try {
      const lane = await h.caller.lanes.create({ cwd: plain.path });
      await expect(h.caller.workspaces.create({ fromLaneId: lane.id })).rejects.toMatchObject({
        code: 'BAD_REQUEST',
        message: expect.stringContaining('not a git repository'),
      });
      // And the preview says so without throwing, so the dialog can explain rather than error.
      const preview = await h.caller.workspaces.preview({ fromLaneId: lane.id });
      expect(preview.isGitRepo).toBe(false);
      expect(repo.listLanes(h.ctx.db)).toHaveLength(1);
    } finally {
      plain.cleanup();
      await h.cleanup();
    }
  });

  it('refuses a lane it has never heard of, the home folder, and the filesystem root', async () => {
    const h = await harness();
    try {
      await expect(h.caller.workspaces.create({ fromLaneId: 'nope' })).rejects.toMatchObject({
        code: 'NOT_FOUND',
      });
      await expect(h.caller.workspaces.create({ repoRoot: '/' })).rejects.toMatchObject({
        code: 'BAD_REQUEST',
        message: expect.stringContaining('not your home folder'),
      });
      // Exactly one of the two inputs, never both and never neither.
      await expect(h.caller.workspaces.create({ fromLaneId: 'a', repoRoot: '/tmp' })).rejects.toBeTruthy();
      await expect(h.caller.workspaces.create({})).rejects.toBeTruthy();
    } finally {
      await h.cleanup();
    }
  });

  it('removes the checkout it prepared when the session cannot be recorded', async () => {
    const h = await harness();
    try {
      const first = await h.caller.lanes.create({ cwd: h.source.path });
      // Make the insert fail the way a real persistence fault would, after the worktree exists.
      const laneId = 'collide';
      repo.createLane(h.ctx.db, {
        id: laneId,
        name: 'taken',
        cwd: '/tmp',
        permission: 'allowlist',
        createdAt: 1,
      });
      const before = sh(h.source.path, 'worktree', 'list', '--porcelain');
      // Prepare and then fail to persist, by hand, because the id is generated inside the mutation.
      const prepared = await prepareStandaloneWorktree({
        repoRoot: h.source.path,
        baseCommit: h.source.head,
        laneId,
        dataDir: h.data.path,
      });
      expect(existsSync(prepared.path)).toBe(true);
      let threw = false;
      try {
        h.ctx.db.transaction((tx) => {
          repo.createLane(tx, {
            id: laneId,
            name: 'dup',
            cwd: prepared.path,
            permission: 'allowlist',
            createdAt: 2,
          });
        });
      } catch {
        threw = true;
        const { removeWorktree } = await import('@/server/git/worktrees');
        await removeWorktree(h.source.path, prepared.path, prepared.branch);
      }
      expect(threw).toBe(true);
      // Nothing had run in it, so the rollback destroys no work — and the repository is as it was.
      expect(existsSync(prepared.path)).toBe(false);
      expect(sh(h.source.path, 'worktree', 'list', '--porcelain')).toBe(before);
      expect(sh(h.source.path, 'branch', '--list', prepared.branch)).toBe('');
      expect(first.id).toBeTruthy();
    } finally {
      await h.cleanup();
    }
  });

  it('cleans up its own branch and directory when the checkout itself fails part-way', async () => {
    const h = await harness();
    try {
      // A `post-checkout` hook that fails is the realistic version of this: `git worktree add`
      // creates the branch and the directory and *then* fails, leaving both behind under a name no
      // retry can reuse.
      const hooks = join(h.source.path, '.git', 'hooks');
      writeFileSync(join(hooks, 'post-checkout'), '#!/bin/sh\nexit 1\n', { mode: 0o755 });
      await expect(
        prepareStandaloneWorktree({
          repoRoot: h.source.path,
          baseCommit: h.source.head,
          laneId: 'hooked',
          dataDir: h.data.path,
        }),
      ).rejects.toBeTruthy();
      expect(existsSync(join(h.data.path, 'worktrees', 'session', 'hooked'))).toBe(false);
      expect(sh(h.source.path, 'branch', '--list', 'mc/session/hooked')).toBe('');
    } finally {
      await h.cleanup();
    }
  });

  it('refuses to start a session once the server is shutting down', async () => {
    const h = await harness();
    const lane = await h.caller.lanes.create({ cwd: h.source.path });
    await h.ctx.close();
    // Ownership runs through `ctx.work`, which closes admission first: a checkout must never be
    // created after shutdown has taken its inventory of what it owns.
    await expect(h.caller.workspaces.create({ fromLaneId: lane.id })).rejects.toThrow(/shutting down/);
    h.data.cleanup();
    h.source.cleanup();
  });

  it("keeps a session's folder fixed, so a managed checkout cannot be orphaned", async () => {
    const h = await harness();
    try {
      const first = await h.caller.lanes.create({ cwd: h.source.path });
      const made = await h.caller.workspaces.create({ fromLaneId: first.id });
      // There is no way to move either one: `lanes.update` renames and nothing else, so a
      // checkout this console created can never be left with nothing pointed at it.
      expect((await h.caller.lanes.update({ laneId: made.lane.id, name: 'Renamed' })).cwd).toBe(
        made.lane.cwd,
      );
      expect((await h.caller.lanes.update({ laneId: first.id, name: 'Also renamed' })).cwd).toBe(
        h.source.path,
      );
    } finally {
      await h.cleanup();
    }
  });
});

describe('resolveRepoRoot', () => {
  it('names the main working tree from inside a linked worktree', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    try {
      const { worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'g',
        count: 1,
        dataDir: data.path,
      });
      const linked = worktrees[0];
      if (!linked) throw new Error('expected a worktree');
      const expected = await git(source.path, ['rev-parse', '--show-toplevel']);
      // Both checkouts agree about which project they are in, which is what lets a session started
      // from either one land in the same repository.
      expect(await resolveRepoRoot(source.path)).toBe(expected);
      expect(await resolveRepoRoot(linked.path)).toBe(expected);
      // Not the linked checkout itself, which is what `--show-toplevel` would have answered there.
      expect(await resolveRepoRoot(linked.path)).not.toBe(linked.path);
    } finally {
      data.cleanup();
      source.cleanup();
    }
  });

  it('refuses a bare repository, which has no working tree to branch from', async () => {
    const dir = makeTmpDir('mc-bare-');
    try {
      execFileSync('git', ['init', '-q', '--bare', dir.path]);
      await expect(resolveRepoRoot(dir.path)).rejects.toThrow(/bare/);
    } finally {
      dir.cleanup();
    }
  });
});

describe('describeCheckout', () => {
  it('reports the commit, its subject, and how many files would be left behind', async () => {
    const source = makeTmpGitRepo();
    try {
      const clean = await describeCheckout(source.path);
      expect(clean.baseCommit).toBe(source.head);
      expect(clean.subject).toBe('init');
      expect(clean.dirtyFiles).toBe(0);

      writeFileSync(join(source.path, 'README.md'), '# changed\n');
      writeFileSync(join(source.path, 'new.txt'), 'untracked\n');
      const dirty = await describeCheckout(source.path);
      // Tracked modifications and untracked files alike: both stay behind, so both are counted.
      expect(dirty.dirtyFiles).toBe(2);
    } finally {
      source.cleanup();
    }
  });
});
