import { spawn } from 'node:child_process';
import { OWN_PROCESS_GROUP, ownGroup, reapGroup } from '@/server/process/group';

export class GitError extends Error {
  constructor(
    message: string,
    readonly args: string[],
    readonly stderr: string,
  ) {
    super(message);
    this.name = 'GitError';
  }
}

/**
 * How long one git command may take before Compare gives up on it.
 *
 * Every git command here runs inside a worktree the operator controls, and a repository can make
 * git wait indefinitely on something that is not git's fault: a `clean`/`smudge` filter that never
 * returns, a credential prompt, a network-backed filesystem. Compare serves one comparison per
 * race at a time, so one such command would pin that race for as long as the server lived, with
 * nothing to show for it. Thirty seconds is far longer than a diff of a working tree takes and
 * short enough that the operator learns something is wrong.
 */
const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * How long the cleanup after an abandoned command is given: SIGTERM, then SIGKILL after this, then
 * this again to confirm the group is empty. A command's total worst case is therefore its timeout
 * plus twice this.
 */
const REAP_GRACE_MS = 2_000;

export interface GitOptions {
  /** Extra environment for this invocation, merged over `process.env` (e.g. `GIT_INDEX_FILE`). */
  env?: Record<string, string>;
  /** Override the per-command deadline; `0` disables it. */
  timeoutMs?: number;
  /** The executable to run. Only a test substitutes this, to stand in for a git that hangs. */
  bin?: string;
  /** Override the cleanup budget; only a test needs this. */
  reapGraceMs?: number;
  /** Override the output cap; only a test needs this. */
  maxOutputChars?: number;
  /**
   * Abandon the command when this is aborted — the caller has stopped wanting the answer.
   *
   * Shutdown is the reason this exists. A comparison's own deadline is per command, so a shutdown
   * that only waited would wait for every remaining command in every queued lane. Aborting says so
   * directly, and the abandonment then follows the same contract as a timeout: the group is cleaned
   * up, and the promise settles only once that cleanup has been decided.
   */
  signal?: AbortSignal;
}

/**
 * Run one git command.
 *
 * The ownership contract, which is the part worth stating:
 *
 * - A command that *finishes* is finished. Whatever it left running is git's business, and the
 *   promise settles at once.
 * - A command this server *abandons* — a timeout, a failure to read its output — is this server's
 *   to clean up, and cleanup means the whole process group it was given, not just the leader. Git
 *   runs the repository's `clean`/`smudge` filters as its own children, and those routinely ignore
 *   the signals git itself answers: the reproduction had the leader exit politely on SIGTERM while
 *   its filter was still alive two and a half seconds later.
 * - The promise does not settle until that cleanup has been decided. Settling first and reaping
 *   afterwards would release Compare's concurrency gate while an unowned kill was still in flight,
 *   which is how a bounded pool starts an unbounded number of subprocesses. The wait is bounded by
 *   `reapGraceMs`, and if the group still cannot be confirmed empty the error says so rather than
 *   implying a clean failure.
 */
export function git(cwd: string, args: string[], opts?: GitOptions): Promise<string> {
  const env = opts?.env ? { ...process.env, ...opts.env } : undefined;
  const timeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const reapGraceMs = opts?.reapGraceMs ?? REAP_GRACE_MS;
  const maxOutputChars = opts?.maxOutputChars ?? MAX_OUTPUT_CHARS;
  const signal = opts?.signal;
  // Nothing is spawned once the caller has already given up; a shutdown that has begun must not
  // start the next command of a comparison it is waiting for.
  if (signal?.aborted)
    return Promise.reject(
      new GitError(`git ${args.join(' ')} was not started: ${abortReason(signal)}`, args, ''),
    );
  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    let overflowed = false;
    // `spawn`, not `execFile`: `execFile` forwards only a fixed set of options to `spawn` and
    // `detached` is not among them, so the group this cleanup depends on would never have existed.
    // Buffering the output here is the price, and it makes the overflow path ours to own as well.
    // Git comes from the host (or the test seam), not the application bundle.
    const child = spawn(/* turbopackIgnore: true */ opts?.bin ?? 'git', args, {
      cwd,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      // The command's own group, so cleanup can reach the filters it starts.
      detached: OWN_PROCESS_GROUP,
    });

    // Identity, liveness and signalling all come from `ownGroup`: a command whose spawn failed has
    // no pid, and is therefore never signalled (see that function for what signalling one did).
    const group = ownGroup(child);

    /** Abandon the command: clean up what it started, then report. */
    const fail = async (message: string): Promise<void> => {
      settled = true;
      clearTimeout(deadline);
      signal?.removeEventListener('abort', onAbort);
      const cleaned = await reapGroup(group, { graceMs: reapGraceMs });
      reject(
        new GitError(
          cleaned
            ? message
            : `${message} (and its processes could not be confirmed stopped; process group ${group.pgid})`,
          args,
          stderr,
        ),
      );
    };

    const collect = (stream: NodeJS.ReadableStream | null, take: (chunk: string) => void) => {
      if (!stream) return;
      stream.setEncoding('utf8');
      stream.on('data', (chunk: string) => {
        if (settled || overflowed) return;
        take(chunk);
        if (stdout.length + stderr.length <= maxOutputChars) return;
        // The command is producing more than this can hold. Abandoning it is the honest answer,
        // and abandoning it means cleaning up its group like any other abandonment.
        overflowed = true;
        void fail(`git ${args.join(' ')} produced more than ${maxOutputChars} characters`);
      });
    };
    collect(child.stdout, (chunk) => {
      stdout += chunk;
    });
    collect(child.stderr, (chunk) => {
      stderr += chunk;
    });

    const onAbort = () => {
      if (settled) return;
      void fail(`git ${args.join(' ')} was cancelled: ${abortReason(signal)}`);
    };
    signal?.addEventListener('abort', onAbort, { once: true });

    // Our own deadline: a process that ignores signals never reaches `close`, so waiting for the
    // usual completion path would leave this promise pending for ever.
    const deadline =
      timeoutMs > 0
        ? setTimeout(() => {
            if (settled) return;
            void fail(`git ${args.join(' ')} did not finish within ${timeoutMs} ms`);
          }, timeoutMs)
        : undefined;

    child.on('error', (err) => {
      if (settled) return;
      void fail(`git ${args.join(' ')} failed: ${err.message}`);
    });

    child.on('close', (code, killedBy) => {
      if (settled) return;
      if (code === 0) {
        settled = true;
        clearTimeout(deadline);
        signal?.removeEventListener('abort', onAbort);
        resolve(stdout.replace(/\n$/, ''));
        return;
      }
      // A command that exited by itself has finished; there is nothing of ours left to reap, and
      // `fail` will confirm that in a single check before rejecting.
      void fail(
        `git ${args.join(' ')} failed: ${stderr.trim() || `exited with ${killedBy ?? `code ${code}`}`}`,
      );
    });
  });
}

/** Why the caller gave up, in the words it used, so the error says who abandoned the command. */
function abortReason(signal: AbortSignal | undefined): string {
  const reason: unknown = signal?.reason;
  if (reason === undefined) return 'cancelled';
  return reason instanceof Error ? reason.message : String(reason);
}

/**
 * How much output one git command may produce before it is abandoned. A diff of a working tree is
 * the largest thing asked for here; anything past this is a repository doing something Compare
 * cannot usefully show, and holding it in memory helps nobody.
 */
const MAX_OUTPUT_CHARS = 64 * 1024 * 1024;
