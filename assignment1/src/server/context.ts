import type Database from 'better-sqlite3';
import type { RunStatus } from '@/core/types';
import { CompareService } from './compare/service';
import type { Config } from './config';
import { type Db, openDb } from './db';
import * as repo from './db/repo';
import type { ProcessControl } from './process/group';
import { ProcessManager } from './process/manager';
import { Replayer } from './replay';
import { OwnedWork } from './work';
import { Hub } from './ws/hub';

/**
 * What shutdown was able to confirm.
 *
 * `ok` means every operation this server owned has settled and its database is closed. Anything
 * else is a failure to report — the caller exits non-zero — and `unfinished` names what was still
 * running. In that case the database is deliberately *left open*: the work still using it is real,
 * and closing the connection under it would replace "we could not confirm cleanup" with a spurious
 * "the database connection is not open".
 *
 * "Every operation" includes the agent processes. A run whose process group could not be confirmed
 * gone is an agent that may still be working in an operator's directory with nothing left to stop
 * it, so it belongs in `unfinished` exactly as an unfinished comparison does — the earlier version
 * of this contract reported `ok: true` while the process manager was logging that very warning.
 */
export interface ShutdownReport {
  ok: boolean;
  unfinished: string[];
}

/** How long shutdown waits for the request work it owns (a fan-out preparing worktrees). */
const WORK_BUDGET_MS = 30_000;

export interface ServerContext {
  config: Config;
  db: Db;
  sqlite: Database.Database;
  hub: Hub;
  processes: ProcessManager;
  replayer: Replayer;
  compare: CompareService;
  /** Effectful request work whose lifetime outlives its response; see `OwnedWork`. */
  work: OwnedWork;
  runStatus(runId: string): RunStatus | undefined;
  close(): Promise<ShutdownReport>;
}

const KEY = Symbol.for('mission-control.server-context');

/**
 * What a test substitutes to exercise shutdown, stated here rather than reached for through private
 * fields: a git that can be made to hang, budgets short enough that an unconfirmable cleanup
 * happens in milliseconds instead of in the real 40 seconds, and the seam through which the process
 * manager reaches the OS — so "this group cannot be confirmed gone" can be produced from an `EPERM`
 * answer rather than from a process that really cannot be killed.
 */
export interface ContextSeams {
  gitBin?: string;
  gitTimeoutMs?: number;
  shutdownBudgetMs?: number;
  processControl?: ProcessControl;
  processCancelGraceMs?: number;
}

export function createServerContext(config: Config, seams: ContextSeams = {}): ServerContext {
  const { db, sqlite } = openDb(config.dbFile);
  repo.failOrphanRuns(db, Date.now(), 'server restarted');
  const hub = new Hub({ db });
  const processes = new ProcessManager({
    db,
    hub,
    claudeBin: config.claudeBin,
    allowedTools: config.allowedTools,
    control: seams.processControl,
    cancelGraceMs: seams.processCancelGraceMs,
  });
  const replayer = new Replayer({ db, hub });
  const compare = new CompareService({ db, gitBin: seams.gitBin, gitTimeoutMs: seams.gitTimeoutMs });
  const work = new OwnedWork();
  // One close, however many callers ask for it. Two signals arriving together used to run this
  // twice: the second `sqlite.close()` landed on a connection the first had already closed, and
  // whatever the first was still reaping lost its owner half way through.
  let closing: Promise<ShutdownReport> | null = null;
  const ctx: ServerContext = {
    config,
    db,
    sqlite,
    hub,
    processes,
    replayer,
    compare,
    work,
    runStatus: (runId) => repo.getRun(db, runId)?.status as RunStatus | undefined,
    close: () => {
      closing ??= (async () => {
        // A paced replay is a chain of timers that write through this connection. Ending them
        // first — the same bounded path a user's Stop takes — means nothing new is queued against
        // the database while the rest of shutdown runs.
        replayer.shutdown();
        // Then everything this server owns, in parallel, each with admission already closed inside
        // it: a run cannot start after `processes.shutdown` begins, a comparison cannot after
        // `compare.shutdown` does, and no request work is admitted once `work.settle` does. That is
        // what makes each wait finite. Comparisons are *cancelled*, not merely waited for.
        const [agents, comparisonsSettled, unfinishedWork] = await Promise.all([
          processes.shutdown(),
          seams.shutdownBudgetMs === undefined
            ? compare.shutdown()
            : compare.shutdown(seams.shutdownBudgetMs),
          work.settle(seams.shutdownBudgetMs ?? WORK_BUDGET_MS),
        ]);
        const unfinished = [
          ...unfinishedWork,
          ...(comparisonsSettled ? [] : compare.runningComparisons().map((id) => `comparison ${id}`)),
          // An agent the escalation could not confirm gone. Named by the run and the group that
          // was being signalled, because that is what an operator needs to go and look for.
          ...agents.unconfirmed.map((p) => `agent process group ${p.pgid ?? 'unknown'} of run ${p.runId}`),
        ];
        await hub.close();
        if ((globalThis as Record<PropertyKey, unknown>)[KEY] === ctx)
          delete (globalThis as Record<PropertyKey, unknown>)[KEY];
        if (unfinished.length > 0) {
          // Everything below this line needs the connection these operations are still using.
          // Closing it now would not make them stop; it would only make them fail in a way that
          // looks like a database fault instead of like the unfinished shutdown it is.
          console.error(
            `shutdown could not confirm cleanup; still running: ${unfinished.join(', ')}. ` +
              'Leaving the database open rather than failing that work under it.',
          );
          return { ok: false, unfinished };
        }
        sqlite.close();
        return { ok: true, unfinished: [] };
      })();
      return closing;
    },
  };
  return ctx;
}

export function registerServerContext(ctx: ServerContext): void {
  (globalThis as Record<PropertyKey, unknown>)[KEY] = ctx;
}

export function getServerContext(): ServerContext {
  const ctx = (globalThis as Record<PropertyKey, unknown>)[KEY] as ServerContext | undefined;
  if (!ctx)
    throw new Error(
      'Mission Control server context is not initialised; start the app with `pnpm dev` or `pnpm start`, not `next dev`.',
    );
  return ctx;
}
