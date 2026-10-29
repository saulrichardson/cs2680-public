import { existsSync, lstatSync, mkdirSync, readdirSync, realpathSync, rmSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { git } from './exec';

export { git } from './exec';

/**
 * How long the git commands that undo a half-prepared race may take.
 *
 * Deliberately shorter than the ordinary command budget, and deliberately *not* cancellable:
 * rollback is what runs after the preparation was abandoned, so cancelling it would be cancelling
 * the cleanup. Bounded so a shutdown that has already given up on preparing cannot then be held
 * open indefinitely by tidying it away.
 */
const ROLLBACK_TIMEOUT_MS = 10_000;

export async function isGitRepo(path: string): Promise<boolean> {
  try {
    return (await git(path, ['rev-parse', '--is-inside-work-tree'])) === 'true';
  } catch {
    return false;
  }
}

export async function headCommit(repoRoot: string, signal?: AbortSignal): Promise<string> {
  return git(repoRoot, ['rev-parse', 'HEAD'], { signal });
}

/**
 * The main working tree of the repository `dir` belongs to, as git itself names it.
 *
 * Every checkout of a repository is a peer — a linked worktree is a perfectly good place to run
 * `git worktree add` from, and `git worktree list` answers with the same set wherever it is run.
 * What this function is for is a *stable name* for the repository, so that two sessions started
 * from two different checkouts agree about which project they are in and the interface can show
 * one path for it.
 *
 * `--show-toplevel` is the wrong question: inside a linked worktree it answers with that checkout,
 * so a second session started from a racing lane would be labelled with the lane's own directory
 * rather than the project. Deriving the answer from `--git-common-dir` is wrong too, which is worth
 * saying because it is the obvious shortcut: the parent of the common git directory is the main
 * working tree only in the ordinary layout, and is something else under `--separate-git-dir` or for
 * a submodule whose git directory lives in the superproject's `.git/modules`.
 *
 * So git is asked directly. `worktree list` puts the main working tree first, and `-z` is what makes
 * parsing it safe: without it git *quotes* paths containing unusual characters, and a path holding a
 * newline would be split across records. With NUL terminators the bytes are the path.
 *
 * A bare repository has no working tree to be named, and is refused rather than resolved to whatever
 * directory happens to contain it.
 */
export async function resolveRepoRoot(dir: string, signal?: AbortSignal): Promise<string> {
  // `-z`: NUL after every attribute and an empty record between worktrees. Never trimmed — a
  // trailing space is a legal directory name, and trimming would quietly rename the project.
  const listing = await git(dir, ['worktree', 'list', '--porcelain', '-z'], { signal });
  const first = listing
    .split('\0')
    .find((field) => field.startsWith('worktree '))
    ?.slice('worktree '.length);
  if (first === undefined || first === '') throw new Error(`${dir} is not part of a git working tree`);
  if (await isBare(dir, signal))
    throw new Error(`${dir} belongs to a bare repository, which has no working tree to branch from`);
  return first;
}

async function isBare(dir: string, signal?: AbortSignal): Promise<boolean> {
  return (await git(dir, ['rev-parse', '--is-bare-repository'], { signal })) === 'true';
}

export interface PreparedWorktree {
  index: number;
  path: string;
  branch: string;
}

/** What was at the target path before a checkout was attempted there. */
type PathState = 'absent' | 'empty' | 'occupied';

/**
 * One checkout, or the repository exactly as this call found it.
 *
 * `git worktree add` is not all-or-nothing: it creates the branch, registers the worktree and
 * populates the directory before running the repository's `post-checkout` hook, so a hook that
 * exits non-zero — or a disk that fills, or an abort part-way — fails the command with all three
 * already in place. Both the race and the independent-session paths go through here so they cannot
 * answer that differently.
 *
 * Rollback removes **only what this call created**. What was already there is established first,
 * from git's own ref inventory and from the filesystem, and a failure to establish it is thrown
 * rather than read as "absent": not knowing is not evidence of ownership, and the branch this
 * collided with is usually the work somebody wanted kept. Anything cleanup cannot remove is named
 * in the error instead of being passed over as a complete rollback.
 */
export async function createCheckout(req: {
  repoRoot: string;
  /** Absolute path for the new checkout. Its parent is created if missing. */
  path: string;
  branch: string;
  baseCommit: string;
  signal?: AbortSignal;
}): Promise<{ path: string; branch: string }> {
  // Before anything is touched, and deliberately allowed to throw: `createCheckout` has created
  // nothing at this point, so refusing is free, while guessing would license a deletion.
  const before = {
    branch: await branchExists(req.repoRoot, req.branch, req.signal),
    path: pathState(req.path),
    registered: await isRegisteredWorktree(req.repoRoot, req.path, req.signal),
  };
  // Somebody else's checkout already lives at this path, as git records it. Its *directory* may be
  // gone — moved, or removed by hand — which makes the filesystem look free while git still points
  // here. Creating on top would fail and the rollback would then unregister a checkout this call
  // never made, so the answer is to refuse before anything is mutated.
  if (before.registered)
    throw new Error(
      `${req.path} is already a registered worktree of ${req.repoRoot}. Remove it with \`git worktree remove\` (or \`git worktree prune\` if its directory is gone) before reusing this path.`,
    );
  mkdirSync(dirname(req.path), { recursive: true });
  try {
    await git(req.repoRoot, ['worktree', 'add', '-b', req.branch, req.path, req.baseCommit], {
      signal: req.signal,
    });
  } catch (err) {
    const retained = await removeCheckoutResources(req.repoRoot, {
      // Something was already at this path, so git cannot have populated it and it is not ours.
      // An *empty* directory git may legitimately have used, so that one is ours to clear — and it
      // is put back empty afterwards, which is how it was found.
      path: before.path === 'occupied' ? null : req.path,
      branch: before.branch ? null : req.branch,
    });
    if (before.path === 'empty' && !existsSync(req.path)) mkdirSync(req.path, { recursive: true });
    if (retained.length > 0) throw new IncompleteRollbackError(err, retained);
    throw err;
  }
  return { path: req.path, branch: req.branch };
}

/** A checkout failed *and* part of what it created is still there. Says which part. */
export class IncompleteRollbackError extends Error {
  constructor(
    override readonly cause: unknown,
    /** Exact resources a human now has to deal with, not a count. */
    readonly retained: string[],
  ) {
    super(
      `${cause instanceof Error ? cause.message : String(cause)} — and this could not be removed afterwards: ${retained.join('; ')}`,
    );
    this.name = 'IncompleteRollbackError';
  }
}

/**
 * Remove a checkout's registration, directory and branch; answer with whatever survived.
 *
 * `git worktree remove` takes the registration and the directory together, which is why it is
 * tried first. When it fails, git's own inventory decides what is actually left: a command that
 * failed before registering anything has nothing to retain, and a registration that really is
 * still there is reported rather than swept up by a repository-wide `git worktree prune` — that
 * prune is not this call's to make, and would take any other stale registration with it.
 *
 * Not cancellable, and separately bounded: this runs after the attempt was abandoned, so
 * cancelling it would be cancelling the cleanup.
 */
async function removeCheckoutResources(
  repoRoot: string,
  res: { path: string | null; branch: string | null },
): Promise<string[]> {
  const opts = { timeoutMs: ROLLBACK_TIMEOUT_MS };
  const retained: string[] = [];
  if (res.path !== null) {
    const removed = await git(repoRoot, ['worktree', 'remove', '--force', res.path], opts).then(
      () => true,
      () => false,
    );
    if (!removed) {
      if (await isRegisteredWorktree(repoRoot, res.path)) retained.push(`worktree ${res.path}`);
      // Registered nowhere, so whatever is on disk is only a directory, and it is ours.
      else rmSync(res.path, { recursive: true, force: true });
    }
  }
  if (res.branch !== null) {
    const deleted = await git(repoRoot, ['branch', '-D', res.branch], opts).then(
      () => true,
      () => false,
    );
    // `branch -D` also fails when there is no such branch, which is the outcome asked for; ask.
    if (!deleted && (await branchExists(repoRoot, res.branch).catch(() => true)))
      retained.push(`branch ${res.branch}`);
  }
  return retained;
}

/**
 * Does this repository already have this branch?
 *
 * A successful inventory, compared exactly. `for-each-ref` patterns match at `/` boundaries, so
 * `refs/heads/mc/session/a` would also list `refs/heads/mc/session/a/b`; the answer is the exact
 * line or nothing. A git that fails to answer throws, because "the command errored" and "the
 * branch is absent" must not be the same result — one of them authorises a deletion.
 */
async function branchExists(repoRoot: string, branch: string, signal?: AbortSignal): Promise<boolean> {
  const ref = `refs/heads/${branch}`;
  const listing = await git(repoRoot, ['for-each-ref', '--format=%(refname)', ref], { signal });
  return listing.split('\n').includes(ref);
}

/**
 * Is this exact path one of the repository's registered worktrees, as git itself lists them?
 *
 * `git worktree list` reports a registration whose directory has vanished as well as a live one,
 * which is the case that matters: the filesystem says the path is free and git does not.
 *
 * A listing that cannot be read answers `true`. Both callers treat that as "not ours" — the
 * preflight refuses and the rollback reports the path — which is the safe direction when the
 * alternative is deleting something git may still be pointing at.
 */
async function isRegisteredWorktree(repoRoot: string, path: string, signal?: AbortSignal): Promise<boolean> {
  const listing = await git(repoRoot, ['worktree', 'list', '--porcelain', '-z'], {
    timeoutMs: ROLLBACK_TIMEOUT_MS,
    ...(signal ? { signal } : {}),
  }).catch(() => null);
  if (listing === null) return true;
  const registered = listing
    .split('\0')
    .filter((field) => field.startsWith('worktree '))
    .map((field) => field.slice('worktree '.length));
  return registered.some((p) => samePath(p, path));
}

/** Two paths naming the same place, allowing for a symlinked parent (macOS `/var` → `/private/var`). */
function samePath(a: string, b: string): boolean {
  if (resolve(a) === resolve(b)) return true;
  const real = (p: string) => {
    try {
      return join(realpathSync(dirname(p)), basename(p));
    } catch {
      return resolve(p);
    }
  };
  return real(a) === real(b);
}

/**
 * Whether the target path is free for this call to own.
 *
 * Only a genuinely empty directory may be taken over. A file, a populated directory, a directory
 * that cannot be read, and a symlink — including a dangling one, which `existsSync` and a plain
 * `stat` both read as nothing at all — are `occupied`: somebody put them there, and rollback
 * leaves them alone.
 */
function pathState(path: string): PathState {
  let entry: ReturnType<typeof lstatSync>;
  try {
    entry = lstatSync(path);
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'ENOENT' ? 'absent' : 'occupied';
  }
  if (!entry.isDirectory()) return 'occupied';
  try {
    return readdirSync(path).length === 0 ? 'empty' : 'occupied';
  } catch {
    return 'occupied';
  }
}

/**
 * Create one worktree per racing lane, or none at all.
 *
 * `signal` is how shutdown reaches this. Preparing a race is several sequential git commands, each
 * of which can wait on a repository's own hooks and filters, so a shutdown that merely *waited*
 * could outlive its budget while a command it knows how to stop was still running. Aborting stops
 * the command that is running (`git`'s own contract: the process group is reaped before the promise
 * settles) and prevents the next one from starting; the rollback below then still runs, because
 * nothing here has been handed to an agent yet.
 */
export async function prepareWorktrees(opts: {
  repoRoot: string;
  groupId: string;
  count: number;
  dataDir: string;
  signal?: AbortSignal;
}): Promise<{ baseCommit: string; worktrees: PreparedWorktree[] }> {
  if (!(await isGitRepo(opts.repoRoot))) throw new Error(`${opts.repoRoot} is not a git repository`);
  const baseCommit = await headCommit(opts.repoRoot, opts.signal);
  const created: PreparedWorktree[] = [];
  try {
    mkdirSync(join(opts.dataDir, 'worktrees', opts.groupId), { recursive: true });
    for (let index = 0; index < opts.count; index += 1) {
      // `createCheckout` owns the one that fails — including the case where git created the
      // branch and the directory before the failure. Only the ones already handed back are this
      // loop's to undo.
      const { path, branch } = await createCheckout({
        repoRoot: opts.repoRoot,
        path: join(opts.dataDir, 'worktrees', opts.groupId, String(index)),
        branch: `mc/${opts.groupId}/${index}`,
        baseCommit,
        signal: opts.signal,
      });
      created.push({ index, path, branch });
    }
  } catch (err) {
    // Nothing has run in these yet, so removing them destroys no work. What survives removal is
    // named in the error rather than swallowed: it is a checkout somebody now has to deal with.
    const retained: string[] = [];
    for (const w of created)
      retained.push(...(await removeCheckoutResources(opts.repoRoot, { path: w.path, branch: w.branch })));
    if (retained.length > 0) throw new IncompleteRollbackError(err, retained);
    throw err;
  }
  return { baseCommit, worktrees: created };
}

/**
 * One worktree for one independent session, on a branch of its own.
 *
 * The difference from `prepareWorktrees` is not the git command, it is what the checkout is *for*:
 * there is no race, no sibling to be compared against, and no base commit shared with anyone. The
 * base is whatever the checkout the operator started from has committed — its own HEAD, which for a
 * racing lane is that lane's branch tip and for an ordinary project is the branch they are on.
 * Uncommitted work is deliberately not carried across: `git worktree add` does not copy it, and
 * copying it would mean deciding what to do with a half-finished edit the operator can still see in
 * their editor. The interface says so; nothing here pretends otherwise.
 *
 * Returned, not persisted: the caller records the lane and the worktree in one transaction, and
 * removes this again if that fails.
 *
 * Failure is `createCheckout`'s business, and deliberately the same business the race has.
 */
export async function prepareStandaloneWorktree(opts: {
  repoRoot: string;
  baseCommit: string;
  laneId: string;
  dataDir: string;
  signal?: AbortSignal;
}): Promise<{ path: string; branch: string }> {
  return createCheckout({
    repoRoot: opts.repoRoot,
    path: join(opts.dataDir, 'worktrees', 'session', opts.laneId),
    // The same `mc/` namespace the races use, so one sweep of an operator's repository can still
    // find everything this console created there.
    branch: `mc/session/${opts.laneId}`,
    baseCommit: opts.baseCommit,
    signal: opts.signal,
  });
}

/**
 * What an operator is about to branch from, and what will be left behind.
 *
 * `git worktree add <commit>` takes the committed state and nothing else, so any uncommitted work
 * in the source checkout stays exactly where it is — in the source checkout. That is the right
 * default (copying half-finished edits, or committing someone's work for them, are both worse), but
 * it is only honest if the interface says so *before* the session is created, with the number of
 * files it applies to. Hence the dirty count: `--porcelain` lists tracked modifications and
 * untracked files alike, which is what "will not come with you" covers.
 */
export async function describeCheckout(
  dir: string,
  signal?: AbortSignal,
): Promise<{ repoRoot: string; baseCommit: string; subject: string; dirtyFiles: number }> {
  const [repoRoot, baseCommit, subject, status] = await Promise.all([
    resolveRepoRoot(dir, signal),
    headCommit(dir, signal),
    git(dir, ['log', '-1', '--pretty=%s'], { signal }).catch(() => ''),
    git(dir, ['status', '--porcelain'], { signal }),
  ]);
  const dirtyFiles = status.split('\n').filter((l) => l.trim().length > 0).length;
  return { repoRoot, baseCommit, subject, dirtyFiles };
}

/**
 * Dispose of a checkout this console created: its registration, its directory and its branch.
 *
 * Throws naming whatever survived, so a caller that logs the failure says which path or branch is
 * still there. Callers that pass a checkout they did not create would be deleting somebody's work;
 * `createCheckout` is the path that establishes ownership first.
 */
export async function removeWorktree(repoRoot: string, path: string, branch: string): Promise<void> {
  const retained = await removeCheckoutResources(repoRoot, { path, branch });
  if (retained.length > 0) throw new Error(`could not remove ${retained.join('; ')}`);
}
