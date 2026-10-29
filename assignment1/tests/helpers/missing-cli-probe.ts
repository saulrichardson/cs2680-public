/**
 * The sequence that used to kill the server: start a run whose executable does not exist, then stop
 * it and shut down before the spawn's asynchronous `error` has been delivered.
 *
 * This runs in a process of its own, in a process group of its own (`detached`, arranged by the
 * test that spawns it), because the defect it guards against is *this* process being signalled.
 * Asserting it from inside the test runner would take the test runner down with it — which is
 * precisely how the audit recorded the bug (`.tmp/code-audit/missing-cli.mts`).
 *
 * Deliberately not `await pm.start(...)` before cancelling. The spawn failure arrives on a
 * `nextTick`, so any yield at all lets the `error` handler forget the run first and the Stop then
 * has nothing to reach. The dangerous window is the synchronous one, and reaching it is the whole
 * point of the probe.
 *
 * Prints `SURVIVED` and exits 0 when the manager signalled nothing. Before the fix it printed
 * nothing after `STARTED` and died of SIGINT.
 */
import { openDb } from '@/server/db';
import * as repo from '@/server/db/repo';
import { ProcessManager } from '@/server/process/manager';

async function main(): Promise<void> {
  const { db } = openDb(':memory:');
  repo.createLane(db, {
    id: 'lane',
    name: 'probe',
    cwd: process.cwd(),
    permission: 'allowlist',
    createdAt: 1,
  });
  const hub = { publishEvent: () => {}, publishLifecycle: () => {} };
  const pm = new ProcessManager({
    db,
    hub,
    claudeBin: '/nonexistent/mission-control-claude',
    cancelGraceMs: 50,
  });

  const started = pm.start({
    runId: 'probe',
    laneId: 'lane',
    prompt: 'x',
    cwd: process.cwd(),
    permission: 'allowlist',
  });
  console.log(`STARTED pid=${pm.pidOf('probe')}`);
  // Synchronously, inside the window where the child has no identity yet.
  void pm.cancel('probe');
  await started;
  const report = await pm.shutdown();
  console.log(`SHUTDOWN unconfirmed=${report.unconfirmed.length}`);
  console.log(`STATUS ${repo.getRun(db, 'probe')?.status}`);
  console.log('SURVIVED');
}

void main();
