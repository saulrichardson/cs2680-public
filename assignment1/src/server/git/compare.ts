import { copyFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { FileStat, Patch } from '@/core/types';
import { parseUnifiedDiff } from '@/core/unidiff';
import { GitError, type GitOptions, git } from './exec';

/** What a caller of this module may say about *how* its git commands run. */
export type GitRunOptions = Pick<GitOptions, 'timeoutMs' | 'bin' | 'signal'>;

/**
 * A lane's compare error, short enough for the strip. `GitError.message` repeats the whole command
 * and every line of stderr; the first line of stderr is the part that says what actually went
 * wrong ("fatal: not a git repository", "bad object <sha>").
 */
export function describeGitFailure(err: unknown): string {
  if (err instanceof GitError) {
    const first = err.stderr
      .split('\n')
      .map((l) => l.trim())
      .find((l) => l.length > 0);
    return `git failed: ${first ?? err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

/**
 * `git diff --numstat -z` output as file statistics.
 *
 * `-z` is not a detail: without it git separates records with newlines, separates the three columns
 * with tabs, writes a rename as `old => new` (or `prefix/{old => new}suffix`), and quotes anything
 * it cannot express that way. A file actually named `a => b.txt` was read as a rename to `b.txt`,
 * and one with a tab or a newline in its name could not be read at all. With `-z` each record is
 * NUL-terminated, names are never quoted or abbreviated, and a rename writes its two paths as two
 * further NUL-terminated fields — so the bytes say exactly which file changed.
 */
export function parseNumstatZ(raw: string): FileStat[] {
  const fields = raw.split('\0');
  const files: FileStat[] = [];
  let i = 0;
  while (i < fields.length) {
    const record = fields[i];
    i += 1;
    if (record === undefined || record.length === 0) continue;
    const columns = record.split('\t');
    const [added = '', removed = ''] = columns;
    // The name is the rest of the record — joined back with tabs, since a name may contain one.
    // An empty name means git is about to write the rename's two paths as separate fields.
    const inlinePath = columns.slice(2).join('\t');
    let path = inlinePath;
    let renamedFrom: string | undefined;
    if (inlinePath.length === 0) {
      renamedFrom = fields[i];
      i += 1;
      path = fields[i] ?? '';
      i += 1;
    }
    if (path.length === 0) continue;
    const binary = added === '-' || removed === '-';
    files.push({
      path,
      renamedFrom,
      added: binary ? 0 : Number(added),
      removed: binary ? 0 : Number(removed),
      binary,
    });
  }
  return files;
}

/**
 * Diff a worktree (including untracked files) against the fan-out base commit.
 *
 * `add -A` is the only way to get untracked files into a diff, but staging into the worktree's own
 * index would fight the agent still working in it: `index.lock` collisions, the agent's own staged
 * work silently reset, object churn — and the compare view polls this every couple of seconds. So
 * every command here runs against a `GIT_INDEX_FILE` under the OS temp dir.
 *
 * That private index is *seeded from the worktree's real one* rather than started empty. The index
 * is where git keeps the list of tracked files, and some of that list cannot be recovered from the
 * working tree: a file that is tracked despite matching `.gitignore`, or one the agent force-added.
 * Starting empty made `add -A` skip those — so a clean worktree was reported as having deleted them
 * (readiness review R5). Copying the file is read-only: git replaces an index by writing a lock file
 * and renaming it over the old one, so a copy always reads one complete version, and everything
 * written to the *index* afterwards goes to the copy. `core.splitIndex=false` keeps that copy a
 * plain complete index rather than one that refers back to a shared file.
 *
 * What this does *not* avoid: `add -A` hashes the worktree's content, and that writes blob objects
 * into the repository's shared object database under `.git/objects`. Those are unreferenced loose
 * objects — no index, ref or commit of the operator's points at them — so they are inert and
 * ordinary `git gc` prunes them. The claim worth making is the narrow one: the operator's index,
 * refs and working tree are untouched, not that nothing is written under `.git`.
 *
 * The temp dir is removed even when a git call fails.
 */
export async function diffAgainstBase(
  worktreePath: string,
  baseCommit: string,
  opts: GitRunOptions = {},
): Promise<{ files: FileStat[]; patches: Patch[]; raw: string }> {
  const indexDir = await mkdtemp(join(tmpdir(), 'mc-compare-'));
  const privateIndex = join(indexDir, 'index');
  try {
    await seedPrivateIndex(worktreePath, privateIndex, opts);
    const env = { GIT_INDEX_FILE: privateIndex };
    const run = (args: string[]) => git(worktreePath, [...INDEX_CONFIG, ...args], { env, ...opts });
    await run(['add', '-A']);
    const numstat = await run(['diff', '--cached', ...DIFF_FORMAT, '--numstat', '-z', baseCommit]);
    const raw = await run(['diff', '--cached', ...DIFF_FORMAT, baseCommit]);
    return { files: parseNumstatZ(numstat), patches: parseUnifiedDiff(raw), raw };
  } finally {
    await rm(indexDir, { recursive: true, force: true }).catch(() => undefined);
  }
}

/**
 * How the private index is written, regardless of what the repository is configured to do.
 *
 * - `core.splitIndex=false` keeps the private index a plain complete one. (Reading a *shared* index
 *   still works: git resolves `sharedindex.*` from the git directory, not from `GIT_INDEX_FILE`.)
 * - `core.fsmonitor=false` and `core.untrackedCache=false` stop `add -A` from trusting cached
 *   cleanliness flags that were written for the repository's own index and its own monitor
 *   process. A stale flag there would mean a changed or new file quietly missing from the
 *   comparison — the failure mode worth avoiding is under-reporting, not slowness.
 */
const INDEX_CONFIG = [
  '-c',
  'core.splitIndex=false',
  '-c',
  'core.fsmonitor=false',
  '-c',
  'core.untrackedCache=false',
];

/**
 * The diff format this code parses, stated in the command rather than assumed.
 *
 * Compare reads git's output by machine, so the shape of that output is part of the contract and
 * cannot be left to the operator's configuration. Two of their settings really do break it, and
 * both were reproduced:
 *
 * - `diff.mnemonicPrefix=true` writes `i/file.txt` instead of `a/file.txt`, so every patch was
 *   attributed to a path no FileStat had. `--src-prefix`/`--dst-prefix` pin it.
 * - `diff.external` replaces the diff wholesale — the probe's script printed `CUSTOM DIFF` and the
 *   patch list came back empty while the file list said otherwise. `--no-ext-diff` turns it off,
 *   as does `--no-textconv` for per-file `textconv` filters.
 *
 * `diff.noprefix=true` is covered by the explicit prefixes too: it used to happen to work for a
 * root-level file and would have mis-stripped a real `b/` directory.
 *
 * None of this changes the operator's configuration; it only declines to inherit it here.
 */
const DIFF_FORMAT = [
  '--no-color',
  '--no-ext-diff',
  '--no-textconv',
  '--src-prefix=a/',
  '--dst-prefix=b/',
  // rename detection, deterministically, so a renamed file correlates across `files` and `patches`
  '-M',
];

/**
 * Copy the worktree's index to `destination`, so `add -A` starts from the tracked state git itself
 * works from. A worktree with no index file yet (nothing tracked, nothing staged) simply starts
 * empty, which is the same thing said a different way.
 */
async function seedPrivateIndex(
  worktreePath: string,
  destination: string,
  opts: GitRunOptions,
): Promise<void> {
  // `--git-path` resolves the index for a linked worktree too, where it lives under
  // `.git/worktrees/<name>/` rather than in the repository's own git directory.
  const indexPath = resolve(
    worktreePath,
    await git(worktreePath, ['rev-parse', '--git-path', 'index'], opts),
  );
  try {
    await copyFile(indexPath, destination);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
  }
}
