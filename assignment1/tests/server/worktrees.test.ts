import {
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readlinkSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { loadConfig } from '@/server/config';
import {
  git,
  headCommit,
  isGitRepo,
  prepareStandaloneWorktree,
  prepareWorktrees,
  removeWorktree,
} from '@/server/git/worktrees';
import { makeTmpDir, makeTmpGitRepo } from '../helpers/tmp';

describe('worktrees', () => {
  const repo = makeTmpGitRepo();
  const data = makeTmpDir('mc-data-');
  afterAll(() => {
    repo.cleanup();
    data.cleanup();
  });

  it('detects git repos', async () => {
    expect(await isGitRepo(repo.path)).toBe(true);
    expect(await isGitRepo(data.path)).toBe(false);
    expect(await headCommit(repo.path)).toBe(repo.head);
  });

  it('creates N worktrees on their own branches at the base commit', async () => {
    const result = await prepareWorktrees({
      repoRoot: repo.path,
      groupId: 'g1',
      count: 3,
      dataDir: data.path,
    });
    expect(result.baseCommit).toBe(repo.head);
    expect(result.worktrees.map((w) => w.index)).toEqual([0, 1, 2]);
    for (const w of result.worktrees) {
      expect(w.path).toBe(join(data.path, 'worktrees', 'g1', String(w.index)));
      expect(w.branch).toBe(`mc/g1/${w.index}`);
      expect(existsSync(join(w.path, 'README.md'))).toBe(true);
      expect(await git(w.path, ['rev-parse', 'HEAD'])).toBe(repo.head);
      expect(await git(w.path, ['rev-parse', '--abbrev-ref', 'HEAD'])).toBe(w.branch);
    }
    // edits in one worktree do not touch another
    const [first, second] = result.worktrees;
    if (!first || !second) throw new Error('expected two worktrees');
    writeFileSync(join(first.path, 'README.md'), '# changed\n');
    expect(await git(second.path, ['status', '--porcelain'])).toBe('');
  });

  it('is all-or-nothing: a name collision removes what was created and rejects', async () => {
    await expect(
      prepareWorktrees({ repoRoot: repo.path, groupId: 'g1', count: 2, dataDir: data.path }),
    ).rejects.toThrow();
    const list = await git(repo.path, ['worktree', 'list', '--porcelain']);
    expect(list.match(/worktree /g)?.length).toBe(4); // main + the 3 from the previous test, nothing extra
  });

  it('rolls back a partially created batch on later collision, and keeps the branch it collided with', async () => {
    await git(repo.path, ['branch', 'mc/g2/1']);
    const collided = await git(repo.path, ['rev-parse', 'mc/g2/1']);
    await expect(
      prepareWorktrees({ repoRoot: repo.path, groupId: 'g2', count: 2, dataDir: data.path }),
    ).rejects.toThrow();
    expect(existsSync(join(data.path, 'worktrees', 'g2', '0'))).toBe(false);
    expect(await git(repo.path, ['branch', '--list', 'mc/g2/0'])).toBe('');
    // The branch that *caused* the failure was never this call's to delete.
    expect(await git(repo.path, ['rev-parse', 'mc/g2/1'])).toBe(collided);
  });

  it('rejects non-git directories before creating anything', async () => {
    await expect(
      prepareWorktrees({ repoRoot: data.path, groupId: 'g2', count: 1, dataDir: data.path }),
    ).rejects.toThrow(/not a git repository/);
  });

  // R9: `git worktree add` runs with the target repository as its cwd, so a relative data directory
  // would put the tree under *that* repository while the rest of the server looked for it under the
  // app directory. `loadConfig` is the one place that resolves it, so a relative override reaches
  // this function already absolute and the tree lands where the server can find it.
  it('creates worktrees under a relative data directory, resolved against the app directory', async () => {
    const app = makeTmpDir('mc-app-');
    try {
      const config = loadConfig(
        { MISSION_CONTROL_DATA_DIR: 'relative-data' } as unknown as NodeJS.ProcessEnv,
        app.path,
      );
      const result = await prepareWorktrees({
        repoRoot: repo.path,
        groupId: 'grel',
        count: 1,
        dataDir: config.dataDir,
      });
      const w = result.worktrees[0];
      if (!w) throw new Error('prepareWorktrees returned no worktree');
      expect(w.path).toBe(join(app.path, 'relative-data', 'worktrees', 'grel', '0'));
      // it exists where the server will look for it ...
      expect(existsSync(join(w.path, 'README.md'))).toBe(true);
      // ... and not under the repository the worktree was branched from
      expect(existsSync(join(repo.path, 'relative-data'))).toBe(false);
      await removeWorktree(repo.path, w.path, w.branch);
    } finally {
      app.cleanup();
    }
  });

  /**
   * A4. `git worktree add` can fail *after* creating everything.
   *
   * A `post-checkout` hook that exits non-zero fails the command with the branch created, the
   * worktree registered and the directory populated. The standalone path cleaned that up and the
   * fan-out path did not, so a failed race left `mc/<group>/0` on disk and in git with no rows
   * behind it — and the next attempt at that name failed with "branch already exists".
   *
   * Both callers now go through `createCheckout`, so this asserts the same outcome for each.
   */
  describe('when git creates the checkout and then fails', () => {
    const hookRepo = makeTmpGitRepo();
    const hookData = makeTmpDir('mc-hook-data-');
    beforeAll(() => {
      const hook = join(hookRepo.path, '.git', 'hooks', 'post-checkout');
      writeFileSync(hook, '#!/bin/sh\nexit 1\n', { mode: 0o755 });
    });
    afterAll(() => {
      hookRepo.cleanup();
      hookData.cleanup();
    });

    it('leaves no branch, registration or directory behind for a fan-out', async () => {
      await expect(
        prepareWorktrees({ repoRoot: hookRepo.path, groupId: 'hook', count: 2, dataDir: hookData.path }),
      ).rejects.toThrow();
      expect(existsSync(join(hookData.path, 'worktrees', 'hook', '0'))).toBe(false);
      expect(await git(hookRepo.path, ['branch', '--list', 'mc/hook/*'])).toBe('');
      const list = await git(hookRepo.path, ['worktree', 'list', '--porcelain']);
      expect(list).not.toContain('worktrees/hook');
      // ... and the same name is usable again once the hook is out of the way, which is what the
      // leak actually cost.
      rmSync(join(hookRepo.path, '.git', 'hooks', 'post-checkout'));
      const retry = await prepareWorktrees({
        repoRoot: hookRepo.path,
        groupId: 'hook',
        count: 1,
        dataDir: hookData.path,
      });
      expect(existsSync(join(retry.worktrees[0]?.path as string, 'README.md'))).toBe(true);
      await removeWorktree(hookRepo.path, retry.worktrees[0]?.path as string, 'mc/hook/0');
      writeFileSync(join(hookRepo.path, '.git', 'hooks', 'post-checkout'), '#!/bin/sh\nexit 1\n', {
        mode: 0o755,
      });
    });

    it('leaves no branch, registration or directory behind for a standalone session', async () => {
      await expect(
        prepareStandaloneWorktree({
          repoRoot: hookRepo.path,
          baseCommit: hookRepo.head,
          laneId: 'lane1',
          dataDir: hookData.path,
        }),
      ).rejects.toThrow();
      expect(existsSync(join(hookData.path, 'worktrees', 'session', 'lane1'))).toBe(false);
      expect(await git(hookRepo.path, ['branch', '--list', 'mc/session/lane1'])).toBe('');
      expect(await git(hookRepo.path, ['worktree', 'list', '--porcelain'])).not.toContain('lane1');
    });

    it('preserves a branch that already existed under the name it wanted', async () => {
      await git(hookRepo.path, ['branch', 'mc/session/kept']);
      const kept = await git(hookRepo.path, ['rev-parse', 'mc/session/kept']);
      await expect(
        prepareStandaloneWorktree({
          repoRoot: hookRepo.path,
          baseCommit: hookRepo.head,
          laneId: 'kept',
          dataDir: hookData.path,
        }),
      ).rejects.toThrow();
      // The collision is why it failed; deleting it would destroy work this console never made.
      expect(await git(hookRepo.path, ['rev-parse', 'mc/session/kept'])).toBe(kept);
      expect(existsSync(join(hookData.path, 'worktrees', 'session', 'kept'))).toBe(false);
    });

    /**
     * A dangling symlink is something somebody put there. `existsSync` and a plain `stat` both
     * report it as nothing at all, so reading the target path that way would license rollback to
     * delete it — and with it whatever the operator was pointing at.
     */
    it('preserves a dangling symlink sitting at the target path', async () => {
      const target = join(hookData.path, 'worktrees', 'session', 'dangling');
      mkdirSync(dirname(target), { recursive: true });
      symlinkSync(join(hookData.path, 'no-such-destination'), target);
      await expect(
        prepareStandaloneWorktree({
          repoRoot: hookRepo.path,
          baseCommit: hookRepo.head,
          laneId: 'dangling',
          dataDir: hookData.path,
        }),
      ).rejects.toThrow();
      expect(lstatSync(target).isSymbolicLink()).toBe(true);
      expect(readlinkSync(target)).toBe(join(hookData.path, 'no-such-destination'));
      rmSync(target);
    });

    /**
     * An empty directory somebody made is a path `git worktree add` will happily use, so it *is*
     * this call's to clear — but it has to be handed back the way it was found, not deleted.
     */
    it('puts back an empty directory it borrowed', async () => {
      const target = join(hookData.path, 'worktrees', 'session', 'borrowed');
      mkdirSync(target, { recursive: true });
      await expect(
        prepareStandaloneWorktree({
          repoRoot: hookRepo.path,
          baseCommit: hookRepo.head,
          laneId: 'borrowed',
          dataDir: hookData.path,
        }),
      ).rejects.toThrow();
      expect(readdirSync(target)).toEqual([]);
      expect(await git(hookRepo.path, ['branch', '--list', 'mc/session/borrowed'])).toBe('');
    });

    /**
     * A registration whose directory has gone makes the filesystem look free while git still
     * points at the path. Creating on top fails, and the rollback would then unregister a checkout
     * this call never made — so the preflight refuses before anything is mutated.
     */
    it('refuses a path git still has registered, and keeps that registration', async () => {
      const repoRoot = hookRepo.path;
      rmSync(join(repoRoot, '.git', 'hooks', 'post-checkout'));
      const taken = join(hookData.path, 'worktrees', 'session', 'taken');
      await git(repoRoot, ['worktree', 'add', '-b', 'mc/session/taken', taken, hookRepo.head]);
      // Removed from disk by hand, as an operator would; git keeps the registration.
      rmSync(taken, { recursive: true, force: true });
      expect(await git(repoRoot, ['worktree', 'list', '--porcelain'])).toContain(taken);

      await expect(
        prepareStandaloneWorktree({
          repoRoot,
          baseCommit: hookRepo.head,
          laneId: 'taken',
          dataDir: hookData.path,
        }),
      ).rejects.toThrow(/already a registered worktree/);

      // Both the registration and the branch behind it are exactly as they were.
      expect(await git(repoRoot, ['worktree', 'list', '--porcelain'])).toContain(taken);
      expect(await git(repoRoot, ['branch', '--list', 'mc/session/taken'])).toContain('mc/session/taken');

      await git(repoRoot, ['worktree', 'prune']);
      await git(repoRoot, ['branch', '-D', 'mc/session/taken']);
      writeFileSync(join(repoRoot, '.git', 'hooks', 'post-checkout'), '#!/bin/sh\nexit 1\n', {
        mode: 0o755,
      });
    });

    /**
     * Not knowing whether the branch is there is not evidence that it is not. The ownership probe
     * runs before anything is created, so a git that cannot answer refuses the whole call rather
     * than proceeding to a rollback that would be allowed to delete.
     */
    it('refuses rather than guessing when the ownership probe cannot be answered', async () => {
      const notARepo = makeTmpDir('mc-not-a-repo-');
      try {
        await expect(
          prepareStandaloneWorktree({
            repoRoot: notARepo.path,
            baseCommit: hookRepo.head,
            laneId: 'probe',
            dataDir: hookData.path,
          }),
        ).rejects.toThrow(/for-each-ref|not a git repository/);
        expect(existsSync(join(hookData.path, 'worktrees', 'session', 'probe'))).toBe(false);
      } finally {
        notARepo.cleanup();
      }
    });
  });

  it('removes a worktree and its branch', async () => {
    const wt = join(data.path, 'worktrees', 'g1', '2');
    await removeWorktree(repo.path, wt, 'mc/g1/2');
    expect(existsSync(wt)).toBe(false);
    expect(await git(repo.path, ['branch', '--list', 'mc/g1/2'])).toBe('');
  });
});
