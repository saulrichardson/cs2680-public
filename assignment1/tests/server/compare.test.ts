import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { diffAgainstBase, parseNumstatZ } from '@/server/git/compare';
import { git } from '@/server/git/exec';
import { prepareWorktrees } from '@/server/git/worktrees';
import { makeTmpDir, makeTmpGitRepo } from '../helpers/tmp';

describe('parseNumstatZ', () => {
  const stat = (over: Partial<ReturnType<typeof parseNumstatZ>[number]>) => ({
    added: 0,
    removed: 0,
    binary: false,
    renamedFrom: undefined,
    ...over,
  });

  it('reads one record per file', () => {
    expect(parseNumstatZ('3\t1\tREADME.md\0')).toEqual([stat({ path: 'README.md', added: 3, removed: 1 })]);
  });

  it('reads a rename as its own two fields', () => {
    expect(parseNumstatZ('2\t0\t\0src/old.py\0src/new.py\0')).toEqual([
      stat({ path: 'src/new.py', renamedFrom: 'src/old.py', added: 2 }),
    ]);
  });

  it('keeps names that contain the separators git normally uses', () => {
    // the record separator, the column separator, and the text git writes a rename with — all of
    // them legal in a filename, and all of them unambiguous once the records are NUL-terminated
    expect(parseNumstatZ('1\t0\tline\nbreak.txt\0').map((f) => f.path)).toEqual(['line\nbreak.txt']);
    expect(parseNumstatZ('1\t0\twe\tird.txt\0').map((f) => f.path)).toEqual(['we\tird.txt']);
    expect(parseNumstatZ('1\t0\ta => b.txt\0')).toEqual([stat({ path: 'a => b.txt', added: 1, removed: 0 })]);
  });

  it('reports a binary file as binary with no line counts', () => {
    expect(parseNumstatZ('-\t-\tlogo.png\0')).toEqual([stat({ path: 'logo.png', binary: true })]);
  });

  it('is empty for no changes', () => {
    expect(parseNumstatZ('')).toEqual([]);
    expect(parseNumstatZ('\0')).toEqual([]);
  });
});

describe('compare', () => {
  it('diffs a worktree against the base including untracked files, without leaving the index staged', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    const { baseCommit, worktrees } = await prepareWorktrees({
      repoRoot: source.path,
      groupId: 'g',
      count: 2,
      dataDir: data.path,
    });
    const w0 = worktrees[0];
    if (!w0) throw new Error('expected a worktree');
    writeFileSync(join(w0.path, 'README.md'), '# demo\nmore\n');
    mkdirSync(join(w0.path, 'docs'), { recursive: true });
    writeFileSync(join(w0.path, 'docs', 'NEW.md'), 'hello\n');
    const d0 = await diffAgainstBase(w0.path, baseCommit);
    expect(d0.files.map((f) => f.path).sort()).toEqual(['README.md', 'docs/NEW.md']);
    expect(d0.files.find((f) => f.path === 'docs/NEW.md')).toMatchObject({ added: 1, removed: 0 });
    expect(d0.patches).toHaveLength(2);
    expect(
      execFileSync('git', ['-C', w0.path, 'diff', '--cached', '--name-only'], { encoding: 'utf8' }),
    ).toBe('');
    const w1 = worktrees[1];
    if (!w1) throw new Error('expected a second worktree');
    const d1 = await diffAgainstBase(w1.path, baseCommit);
    expect(d1.files).toEqual([]);
    expect(d1.patches).toEqual([]);

    source.cleanup();
    data.cleanup();
  });

  it("leaves the worktree's own index untouched, keeping work the agent staged itself", async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    const { baseCommit, worktrees } = await prepareWorktrees({
      repoRoot: source.path,
      groupId: 'g3',
      count: 1,
      dataDir: data.path,
    });
    const [w0] = worktrees;
    if (!w0) throw new Error('prepareWorktrees returned no worktree');
    // the agent stages one file itself (exactly what a `git add` inside a run does) and leaves
    // another edit unstaged; compare must report both and disturb neither
    writeFileSync(join(w0.path, 'staged.txt'), 'staged by the agent\n');
    execFileSync('git', ['-C', w0.path, 'add', 'staged.txt'], { encoding: 'utf8' });
    writeFileSync(join(w0.path, 'README.md'), '# demo\nunstaged edit\n');

    const d = await diffAgainstBase(w0.path, baseCommit);
    expect(d.files.map((f) => f.path).sort()).toEqual(['README.md', 'staged.txt']);

    // the agent's staging survived, and no lock was left behind for its next git command
    expect(
      execFileSync('git', ['-C', w0.path, 'diff', '--cached', '--name-only'], { encoding: 'utf8' }),
    ).toBe('staged.txt\n');
    const gitDir = execFileSync('git', ['rev-parse', '--absolute-git-dir'], {
      cwd: w0.path,
      encoding: 'utf8',
    }).trim();
    expect(existsSync(join(gitDir, 'index.lock'))).toBe(false);
    source.cleanup();
    data.cleanup();
  });

  /**
   * R5. The private index used to start empty, so anything git knows about only from the index —
   * a file that is tracked despite matching `.gitignore` — was absent from it and `git add -A` did
   * not put it back. Compare then reported a clean worktree as having deleted that file. Seeding
   * the private index from the worktree's real one gives `add -A` the same tracked baseline git
   * itself works from, without ever writing to the real index.
   */
  it('does not report a clean tracked file as deleted just because it matches .gitignore', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    try {
      const inRepo = (...args: string[]) =>
        execFileSync('git', ['-C', source.path, ...args], { encoding: 'utf8' }).trim();
      writeFileSync(join(source.path, '.gitignore'), 'build.log\n');
      writeFileSync(join(source.path, 'build.log'), 'tracked even though ignored\n');
      inRepo('add', '.gitignore');
      inRepo('add', '-f', 'build.log');
      inRepo('commit', '-q', '-m', 'track an ignored file');
      const { baseCommit, worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'gignored',
        count: 1,
        dataDir: data.path,
      });
      const w0 = worktrees[0];
      if (!w0) throw new Error('prepareWorktrees returned no worktree');

      // git itself sees a clean worktree ...
      expect(execFileSync('git', ['-C', w0.path, 'status', '--porcelain'], { encoding: 'utf8' })).toBe('');
      // ... and so must Compare
      expect((await diffAgainstBase(w0.path, baseCommit)).files).toEqual([]);

      // an actual edit to that same file is still reported
      writeFileSync(join(w0.path, 'build.log'), 'changed by the agent\n');
      const edited = await diffAgainstBase(w0.path, baseCommit);
      expect(edited.files.map((f) => f.path)).toEqual(['build.log']);
      expect(edited.patches.map((p) => p.filePath)).toEqual(['build.log']);

      // and so is a real deletion
      rmSync(join(w0.path, 'build.log'));
      const deleted = await diffAgainstBase(w0.path, baseCommit);
      expect(deleted.files).toMatchObject([{ path: 'build.log', added: 0 }]);
    } finally {
      source.cleanup();
      data.cleanup();
    }
  });

  it('includes an ignored file the agent force-added, and leaves the real index byte-for-byte alone', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    try {
      writeFileSync(join(source.path, '.gitignore'), '*.secretlog\n');
      execFileSync('git', ['-C', source.path, 'add', '.gitignore'], { encoding: 'utf8' });
      execFileSync('git', ['-C', source.path, 'commit', '-q', '-m', 'ignore logs'], { encoding: 'utf8' });
      const { baseCommit, worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'gforced',
        count: 1,
        dataDir: data.path,
      });
      const w0 = worktrees[0];
      if (!w0) throw new Error('prepareWorktrees returned no worktree');

      // the agent stages an ignored file deliberately, the way `git add -f` does
      writeFileSync(join(w0.path, 'run.secretlog'), 'deliberately kept\n');
      execFileSync('git', ['-C', w0.path, 'add', '-f', 'run.secretlog'], { encoding: 'utf8' });
      // ... and leaves an ordinary untracked file next to it, which is still ignored
      writeFileSync(join(w0.path, 'other.secretlog'), 'incidental\n');

      const indexFile = join(
        execFileSync('git', ['rev-parse', '--absolute-git-dir'], { cwd: w0.path, encoding: 'utf8' }).trim(),
        'index',
      );
      const before = readFileSync(indexFile);

      const d = await diffAgainstBase(w0.path, baseCommit);
      // the force-added file is part of the work; the one git is ignoring is not
      expect(d.files.map((f) => f.path)).toEqual(['run.secretlog']);

      // the agent's own index is untouched, down to its bytes
      expect(readFileSync(indexFile).equals(before)).toBe(true);
      expect(
        execFileSync('git', ['-C', w0.path, 'diff', '--cached', '--name-only'], { encoding: 'utf8' }),
      ).toBe('run.secretlog\n');
    } finally {
      source.cleanup();
      data.cleanup();
    }
  });

  /**
   * Compare reads git's output by machine, so the *shape* of that output is part of the contract
   * and cannot be inherited from whatever the operator has configured. Two of their settings were
   * reproduced breaking it outright, and one that happened to work by luck.
   */
  describe('against a repository whose diff format is configured', () => {
    const configured = async (settings: [string, string][]) => {
      const source = makeTmpGitRepo();
      const data = makeTmpDir('mc-data-');
      const inRepo = (...args: string[]) =>
        execFileSync('git', ['-C', source.path, ...args], { encoding: 'utf8' }).trim();
      for (const [key, value] of settings) inRepo('config', key, value);
      const { baseCommit, worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'gcfg',
        count: 1,
        dataDir: data.path,
      });
      const w0 = worktrees[0];
      if (!w0) throw new Error('no worktree');
      writeFileSync(join(w0.path, 'README.md'), '# demo\nchanged by the agent\n');
      return {
        w0,
        baseCommit,
        cleanup: () => {
          source.cleanup();
          data.cleanup();
        },
      };
    };

    it('reports the same path in the stats and the patch under diff.mnemonicPrefix', async () => {
      // mnemonicPrefix writes `i/README.md` for an index-side path, so every patch was attributed
      // to a file no FileStat had and the diff view could correlate none of them.
      const r = await configured([['diff.mnemonicPrefix', 'true']]);
      try {
        const d = await diffAgainstBase(r.w0.path, r.baseCommit);
        expect(d.files.map((f) => f.path)).toEqual(['README.md']);
        expect(d.patches.map((p) => p.filePath)).toEqual(['README.md']);
      } finally {
        r.cleanup();
      }
    });

    it('still produces a real patch under diff.external', async () => {
      // An external differ replaces git's output wholesale: the file list said one file had
      // changed while the patch list was empty.
      const tools = makeTmpDir('mc-external-');
      const external = join(tools.path, 'external-diff.sh');
      writeFileSync(external, '#!/bin/sh\nprintf "CUSTOM DIFF\\n"\n', { mode: 0o755 });
      const r = await configured([['diff.external', external]]);
      try {
        const d = await diffAgainstBase(r.w0.path, r.baseCommit);
        expect(d.files.map((f) => f.path)).toEqual(['README.md']);
        expect(d.patches.map((p) => p.filePath)).toEqual(['README.md']);
        expect(d.raw).not.toContain('CUSTOM DIFF');
        expect(d.patches[0]?.hunks.length).toBeGreaterThan(0);
      } finally {
        r.cleanup();
        tools.cleanup();
      }
    });

    it('does not mistake a real b/ directory for a diff prefix under diff.noprefix', async () => {
      // noprefix happened to work for a root-level file. A file actually inside `b/` is the case
      // that would have been mis-stripped to `inside.txt`.
      const r = await configured([['diff.noprefix', 'true']]);
      try {
        mkdirSync(join(r.w0.path, 'b'), { recursive: true });
        writeFileSync(join(r.w0.path, 'b', 'inside.txt'), 'nested\n');
        const d = await diffAgainstBase(r.w0.path, r.baseCommit);
        expect(d.files.map((f) => f.path).sort()).toEqual(['README.md', 'b/inside.txt']);
        expect(d.patches.map((p) => p.filePath).sort()).toEqual(['README.md', 'b/inside.txt']);
      } finally {
        r.cleanup();
      }
    });
  });

  /**
   * A git command this server abandons is this server's to clean up — and cleaning it up means the
   * process group, not the leader.
   *
   * Git runs the repository's `clean`/`smudge` filters as its own children, and those routinely
   * ignore the signals git itself answers. The reproduction is the polite-leader case: the timeout
   * rejected on time, the leader exited on SIGTERM, and its filter was still running two and a half
   * seconds later. Nothing later would ever have collected it.
   */
  describe('cleaning up after a git command that is given up on', () => {
    /** A fake git that starts a stubborn filter. The filter writes its own pid once its handlers
     * are installed, so the file's existence proves it is really ignoring signals. */
    const fakeGit = (dir: string, leader: 'polite' | 'stubborn', pidFile: string) => {
      const filter = [
        "process.on('SIGINT', () => {});",
        "process.on('SIGTERM', () => {});",
        "require('node:fs').writeFileSync(process.argv[1], String(process.pid));",
        'setInterval(() => {}, 1000);',
        // never outlive the test run, whatever happens
        'setTimeout(() => process.exit(0), 20000);',
      ].join('');
      const bin = join(dir, `git-${leader}`);
      writeFileSync(
        bin,
        [
          '#!/usr/bin/env node',
          "const { spawn } = require('node:child_process');",
          `spawn(process.execPath, ['-e', ${JSON.stringify(filter)}, ${JSON.stringify(pidFile)}], { stdio: 'ignore' });`,
          leader === 'stubborn' ? "process.on('SIGTERM', () => {});" : '',
          'setInterval(() => {}, 1000);',
          'setTimeout(() => process.exit(0), 20000);',
        ].join('\n'),
        { mode: 0o755 },
      );
      return bin;
    };
    const alive = (pid: number) => {
      try {
        process.kill(pid, 0);
        return true;
      } catch {
        return false;
      }
    };
    /** Wait for the filter to record itself, which is this fixture's readiness signal. */
    const waitForFilter = async (pidFile: string) => {
      for (let i = 0; i < 250 && !existsSync(pidFile); i += 1) await new Promise((r) => setTimeout(r, 10));
      return existsSync(pidFile);
    };

    it.each(['polite', 'stubborn'] as const)(
      'reaps the whole group when the leader is %s',
      async (leader) => {
        const tools = makeTmpDir('mc-git-group-');
        const pidFile = join(tools.path, 'filter.pid');
        let filter: number | undefined;
        try {
          const bin = fakeGit(tools.path, leader, pidFile);
          // Two node processes have to boot before there is anything to reap, and on a loaded
          // machine that is not instant. So the command is started first, the fixture's own
          // readiness signal is waited for, and only then does the deadline (comfortably longer
          // than that wait) abandon it. A deadline short enough to fire during startup tested the
          // scheduler, not the cleanup: it once failed here with no filter to find.
          const pending = git(tools.path, ['status'], { bin, timeoutMs: 3_000, reapGraceMs: 1_000 });
          expect(await waitForFilter(pidFile), 'the fake filter never started').toBe(true);
          filter = Number(readFileSync(pidFile, 'utf8'));
          expect(alive(filter), 'the fake filter was not running before the command was abandoned').toBe(
            true,
          );

          await expect(pending).rejects.toThrow(/did not finish within 3000 ms/);

          // Nothing else happens after this point: no further git command, no timer of anyone
          // else's. The promise did not settle until cleanup was decided, so by now it is done.
          expect(alive(filter)).toBe(false);
        } finally {
          if (filter !== undefined && alive(filter)) process.kill(filter, 'SIGKILL');
          tools.cleanup();
        }
      },
      60_000,
    );

    it('reaps the group when the command floods its output too', async () => {
      const tools = makeTmpDir('mc-git-flood-');
      const pidFile = join(tools.path, 'flood-filter.pid');
      let filter: number | undefined;
      try {
        const filterCode = [
          "process.on('SIGTERM', () => {});",
          "require('node:fs').writeFileSync(process.argv[1], String(process.pid));",
          'setInterval(() => {}, 1000);',
          'setTimeout(() => process.exit(0), 20000);',
        ].join('');
        const bin = join(tools.path, 'git-flood');
        writeFileSync(
          bin,
          [
            '#!/usr/bin/env node',
            "const { spawn } = require('node:child_process');",
            `spawn(process.execPath, ['-e', ${JSON.stringify(filterCode)}, ${JSON.stringify(pidFile)}], { stdio: 'ignore' });`,
            "const line = 'x'.repeat(64 * 1024);",
            // flood only once the filter has installed its handlers and recorded itself, so the
            // test is about cleanup rather than about which of the two started first
            'setTimeout(() => setInterval(() => process.stdout.write(line), 1), 200);',
            'setTimeout(() => process.exit(0), 20000);',
          ].join('\n'),
          { mode: 0o755 },
        );
        await expect(
          git(tools.path, ['diff'], { bin, timeoutMs: 20_000, reapGraceMs: 400, maxOutputChars: 1024 }),
        ).rejects.toThrow(/produced more than/);
        expect(existsSync(pidFile)).toBe(true);
        filter = Number(readFileSync(pidFile, 'utf8'));
        expect(alive(filter)).toBe(false);
      } finally {
        if (filter !== undefined && alive(filter)) process.kill(filter, 'SIGKILL');
        tools.cleanup();
      }
    }, 30_000);
  });

  it('gives up on a git command that will not finish, and says so', async () => {
    // A repository can make git wait for ever on something that is not git's fault — a `clean`
    // filter that never returns, a credential prompt. Compare serves one comparison per race at a
    // time, so one such command would pin that race for the life of the server.
    const tools = makeTmpDir('mc-hanging-git-');
    const hanging = join(tools.path, 'hanging-git');
    writeFileSync(
      hanging,
      ['#!/usr/bin/env node', "process.on('SIGTERM', () => {});", 'setInterval(() => {}, 1000);'].join('\n'),
      { mode: 0o755 },
    );
    try {
      const started = Date.now();
      await expect(git(tools.path, ['rev-parse', 'HEAD'], { bin: hanging, timeoutMs: 200 })).rejects.toThrow(
        /did not finish within 200 ms/,
      );
      // it really did settle at the deadline, rather than waiting for a process that never exits
      expect(Date.now() - started).toBeLessThan(5_000);
    } finally {
      tools.cleanup();
    }
  }, 20_000);

  it('works against a split index without writing anything into the repository', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    try {
      const { baseCommit, worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'gsplit',
        count: 1,
        dataDir: data.path,
      });
      const w0 = worktrees[0];
      if (!w0) throw new Error('prepareWorktrees returned no worktree');
      // With `core.splitIndex` on, the index file is a link to a shared one in the git directory.
      // A copy of the link still resolves — git looks for the shared index in the git directory,
      // which is found from the worktree either way — and our own writes go to the copy.
      execFileSync('git', ['-C', w0.path, 'update-index', '--split-index'], { encoding: 'utf8' });
      const gitDir = execFileSync('git', ['rev-parse', '--absolute-git-dir'], {
        cwd: w0.path,
        encoding: 'utf8',
      }).trim();
      const shared = () =>
        readdirSync(gitDir)
          .filter((f) => f.startsWith('sharedindex.'))
          .sort();
      const sharedBefore = shared();
      expect(sharedBefore.length).toBeGreaterThan(0);
      const indexBefore = readFileSync(join(gitDir, 'index'));

      writeFileSync(join(w0.path, 'README.md'), '# demo\nchanged\n');
      writeFileSync(join(w0.path, 'brand-new.txt'), 'added\n');
      const d = await diffAgainstBase(w0.path, baseCommit);
      expect(d.files.map((f) => f.path).sort()).toEqual(['README.md', 'brand-new.txt']);

      // the repository is exactly as it was: same index bytes, no extra shared index
      expect(readFileSync(join(gitDir, 'index')).equals(indexBefore)).toBe(true);
      expect(shared()).toEqual(sharedBefore);
    } finally {
      source.cleanup();
      data.cleanup();
    }
  });

  it('reads file names that contain tabs, newlines, and an arrow sequence', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    try {
      const { baseCommit, worktrees } = await prepareWorktrees({
        repoRoot: source.path,
        groupId: 'gnames',
        count: 1,
        dataDir: data.path,
      });
      const w0 = worktrees[0];
      if (!w0) throw new Error('prepareWorktrees returned no worktree');
      // Names git's human-readable output cannot express unambiguously: a tab is the numstat column
      // separator, a newline is its record separator, and " => " is how it writes a rename.
      const names = ['we\tird.txt', 'line\nbreak.txt', 'a => b.txt', 'naïve.txt'];
      for (const name of names) writeFileSync(join(w0.path, name), `content of ${name}\n`);

      const d = await diffAgainstBase(w0.path, baseCommit);
      expect(d.files.map((f) => f.path).sort()).toEqual([...names].sort());
      // none of them is mistaken for a rename
      expect(d.files.every((f) => f.renamedFrom === undefined)).toBe(true);
      // and every patch lands on the same path its FileStat did, so the diff view can correlate them
      expect(d.patches.map((p) => p.filePath).sort()).toEqual([...names].sort());
    } finally {
      source.cleanup();
      data.cleanup();
    }
  });

  it('normalizes a renamed file so its FileStat and Patch correlate on the new path', async () => {
    const source = makeTmpGitRepo();
    const data = makeTmpDir('mc-data-');
    const { baseCommit, worktrees } = await prepareWorktrees({
      repoRoot: source.path,
      groupId: 'g2',
      count: 1,
      dataDir: data.path,
    });
    const w0 = worktrees[0];
    if (!w0) throw new Error('expected a worktree');
    // fs rename (not `git mv`) since diffAgainstBase's own `git add -A` stages it, and -M
    // detects the rename; a content edit alongside it forces real hunks (a pure identical
    // rename produces no "+++"/"---" lines, hence no patch entry to correlate against).
    renameSync(join(w0.path, 'README.md'), join(w0.path, 'README2.md'));
    writeFileSync(join(w0.path, 'README2.md'), '# demo\nmore\n');
    const result = await diffAgainstBase(w0.path, baseCommit);
    const stat = result.files.find((f) => f.path === 'README2.md');
    expect(stat).toMatchObject({ path: 'README2.md', renamedFrom: 'README.md' });
    expect(result.patches.some((p) => p.filePath === 'README2.md')).toBe(true);
    source.cleanup();
    data.cleanup();
  });
});
