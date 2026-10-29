import type { ChildProcess } from 'node:child_process';

/**
 * Owning a process group, rather than a process.
 *
 * Every subprocess this server starts can start subprocesses of its own — the agent runs the
 * operator's tools, `git` runs their `clean`/`smudge` filters — and those routinely outlive the
 * parent that was better behaved about signals. So a spawn this server must clean up gets a
 * process group of its own, and the group is what is signalled and what "gone" is measured
 * against.
 *
 * This module is the only place that decides whether a spawn has an identity, whether anything of
 * it is alive, and how it is signalled. The richer lifecycle — deciding a run's outcome, holding a
 * session while cleanup is unconfirmed — belongs to `ProcessManager`.
 */

/**
 * Whether spawns get a process group of their own. POSIX only: `process.kill(-pid, …)` has no
 * meaning on Windows, which this console does not target (see `docs/approach.md`). Where groups are
 * unavailable, everything below addresses the leader alone and says so.
 */
export const OWN_PROCESS_GROUP = process.platform !== 'win32';

/**
 * How the caller reaches OS processes. The default is `process.kill`; tests substitute it to
 * exercise cleanup that cannot be confirmed, without needing a process that really cannot be killed.
 */
export interface ProcessControl {
  /** As `process.kill`: a negative pid addresses a process group, and signal 0 only asks. */
  kill(pid: number, signal: NodeJS.Signals | 0): void;
}

export const DEFAULT_CONTROL: ProcessControl = { kill: (pid, signal) => process.kill(pid, signal) };

/**
 * A group this server started and is responsible for — or, before the OS has given the spawn an
 * identity, nothing at all.
 */
export interface OwnedGroup {
  /**
   * The group id, which is the leader's pid; `undefined` when there is no identity to address.
   * Read every time rather than copied — a spawn's pid is not knowable before it has one.
   */
  readonly pgid: number | undefined;
  /**
   * Is anything in this group still there?
   *
   * A process group exists for as long as it has a member, so signal 0 to the negative pgid answers
   * for the whole group and keeps answering after the leader has gone. `EPERM` means the group is
   * there but not ours to signal, which still counts as alive: we have not cleaned it up. A spawn
   * with no identity is `false`: nothing of ours is running.
   */
  alive(): boolean;
  /**
   * Signal the whole group — not conditional on the leader still being alive, because the case
   * this exists for is a polite leader that has gone while what it started has not. `ESRCH` means
   * the group is empty, which is the outcome being asked for. A spawn with no identity is never
   * signalled.
   */
  signal(sig: NodeJS.Signals): void;
}

/**
 * What this server owns after spawning `child`.
 *
 * The rule: **a spawn has an identity only when the OS gave it a positive pid, and only an
 * identity may be signalled.** A spawn that failed has none — `pid` is `undefined` while the exit
 * codes are still `null` — and pid `0` is not an identity either: POSIX reads it as the caller's
 * own process group, so signalling it kills this server. No identity, nothing alive, nothing
 * signalled. See `docs/records/2026-09-20-assignment1-reliability-refactor.md`.
 */
export function ownGroup(child: ChildProcess, control: ProcessControl = DEFAULT_CONTROL): OwnedGroup {
  /** The leader's pid, or `undefined` when this spawn has no identity to address. */
  const pidOf = (): number | undefined =>
    typeof child.pid === 'number' && Number.isInteger(child.pid) && child.pid > 0 ? child.pid : undefined;
  const leaderAlive = (): boolean =>
    pidOf() !== undefined && child.exitCode === null && child.signalCode === null;
  return {
    get pgid(): number | undefined {
      return OWN_PROCESS_GROUP ? pidOf() : undefined;
    },
    alive(): boolean {
      const pgid = this.pgid;
      if (pgid === undefined) return leaderAlive();
      try {
        control.kill(-pgid, 0);
        return true;
      } catch (err) {
        return (err as NodeJS.ErrnoException).code === 'EPERM';
      }
    },
    signal(sig: NodeJS.Signals): void {
      const pgid = this.pgid;
      if (pgid !== undefined) {
        try {
          control.kill(-pgid, sig);
          return;
        } catch (err) {
          if ((err as NodeJS.ErrnoException).code === 'ESRCH') return;
        }
      }
      // No group to address (Windows), or the group refused the signal. The leader is the only
      // thing left to try, and only if it exists and is still running.
      if (!leaderAlive()) return;
      try {
        child.kill(sig);
      } catch {
        // the leader is already gone
      }
    },
  };
}

/**
 * End this group and wait, within a budget, for it to be gone.
 *
 * SIGTERM first, SIGKILL after `graceMs`, and the answer is whether the group was actually
 * confirmed empty. `false` is a real outcome, not a failure to report: the caller is expected to
 * say so rather than pretend the work is cleaned up.
 */
export async function reapGroup(
  group: OwnedGroup,
  opts: { graceMs: number; pollMs?: number },
): Promise<boolean> {
  const pollMs = opts.pollMs ?? 25;
  if (!group.alive()) return true;
  group.signal('SIGTERM');
  const killAt = Date.now() + opts.graceMs;
  const deadline = killAt + opts.graceMs;
  let killed = false;
  while (group.alive()) {
    if (!killed && Date.now() >= killAt) {
      group.signal('SIGKILL');
      killed = true;
    }
    if (Date.now() >= deadline) return !group.alive();
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  return true;
}
