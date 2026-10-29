import { beforeEach, describe, expect, it } from 'vitest';
import type { LaneDto, RunSummaryDto } from '@/core/types';
import { selectLaneRuns, useMissionStore } from '@/ui/store/missionStore';
import { callKey } from '@/ui/store/rows';
import { loadFixture } from '../helpers/fixtures';

const lane: LaneDto = {
  id: 'lane1',
  name: 'Lane 1',
  cwd: '/tmp/x',
  permission: 'allowlist',
  groupId: null,
  groupIndex: null,
  createdAt: 1,
  archivedAt: null,
  projectRoot: '/tmp/x',
  lastActivityAt: 1,
  isolated: false,
};
const dto = (runId: string, over: Partial<RunSummaryDto> = {}): RunSummaryDto => ({
  laneId: 'lane1',
  runId,
  browser: 'chrome',
  origin: 'execution',
  status: 'running',
  startedAt: 10,
  prompt: 'fix it',
  cwd: '/tmp/x',
  resumable: false,
  ...over,
});

beforeEach(() => useMissionStore.getState().reset());

describe('missionStore', () => {
  it('hydrates lanes and runs in lane order', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('b', { startedAt: 20 }), dto('a', { startedAt: 10 })]);
    expect(useMissionStore.getState().runsByLane.lane1).toEqual(['a', 'b']);
    expect(useMissionStore.getState().runs.a?.prompt).toBe('fix it');
  });

  it('applies contiguous envelopes and buffers out-of-order ones until the gap fills', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('r')]);
    const envs = loadFixture('flat', { laneId: 'lane1', runId: 'r' });
    s.ingest(envs.slice(5, 10)); // seq 6..10 arrive first
    expect(useMissionStore.getState().runs.r?.lastSeq).toBe(0);
    s.ingest(envs.slice(0, 5)); // seq 1..5 fill the gap
    expect(useMissionStore.getState().runs.r?.lastSeq).toBe(10);
    s.ingest(envs.slice(3, 8)); // duplicates are ignored
    expect(useMissionStore.getState().runs.r?.lastSeq).toBe(10);
    s.ingest(envs.slice(10));
    const view = useMissionStore.getState().runs.r;
    expect(view?.lastSeq).toBe(73);
    expect(view?.outcome).toEqual({ subtype: 'success', isError: false });
    expect(Object.keys(view?.callsById ?? {})).toHaveLength(10);
  });

  it('creates a placeholder for a run first seen over the socket and lists it as unknown', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.ingest(loadFixture('flat', { laneId: 'lane1', runId: 'ghost' }).slice(0, 3));
    expect(useMissionStore.getState().unknownRuns).toEqual(['ghost']);
    expect(useMissionStore.getState().runsByLane.lane1).toEqual(['ghost']);
    s.hydrateRuns([dto('ghost', { prompt: 'real prompt' })]);
    expect(useMissionStore.getState().unknownRuns).toEqual([]);
    expect(useMissionStore.getState().runs.ghost?.prompt).toBe('real prompt');
    expect(useMissionStore.getState().runs.ghost?.lastSeq).toBe(3);
  });

  it('applies lifecycle without regressing a finished run', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('r')]);
    s.ingest(loadFixture('flat', { laneId: 'lane1', runId: 'r' }));
    expect(useMissionStore.getState().runs.r?.status).toBe('running'); // the result event alone ends nothing
    s.applyLifecycle({
      laneId: 'lane1',
      runId: 'r',
      origin: 'execution',
      status: 'finished',
      startedAt: 10,
      endedAt: 90,
    });
    s.applyLifecycle({ laneId: 'lane1', runId: 'r', origin: 'execution', status: 'running', startedAt: 10 });
    expect(useMissionStore.getState().runs.r?.status).toBe('finished');
    s.applyLifecycle({
      laneId: 'lane1',
      runId: 'r',
      origin: 'execution',
      status: 'cancelled',
      startedAt: 10,
      endedAt: 99,
    });
    expect(useMissionStore.getState().runs.r?.status).toBe('cancelled');
  });

  it('keeps a cancelled run cancelled while its stored events hydrate, error result included', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('r', { status: 'cancelled', endedAt: 99 })]);
    s.ingest(loadFixture('failed', { laneId: 'lane1', runId: 'r' })); // ends in an error result
    expect(useMissionStore.getState().runs.r?.status).toBe('cancelled');
    expect(useMissionStore.getState().runs.r?.endedAt).toBe(99);
  });

  /**
   * R1's client half. A lane snapshot is the server's whole picture of a lane, so it both discovers
   * runs and corrects the ones already held — and it does so in one store update, without asking
   * for each newly named run individually (`SocketBridge` refreshes the lane's run list instead).
   */
  it('adopts a lane snapshot as the server sent it', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('known')]);
    expect(useMissionStore.getState().runs.known?.status).toBe('running');

    s.reconcileLane('lane1', [
      {
        laneId: 'lane1',
        runId: 'known',
        origin: 'execution',
        status: 'finished',
        startedAt: 10,
        endedAt: 80,
      },
      { laneId: 'lane1', runId: 'appeared', origin: 'import', status: 'failed', startedAt: 20, endedAt: 25 },
    ]);

    const after = useMissionStore.getState();
    expect(after.runs.known?.status).toBe('finished');
    expect(after.runs.known?.endedAt).toBe(80);
    // the run discovered here keeps what the snapshot said about it, provenance included
    expect(after.runs.appeared).toMatchObject({ status: 'failed', origin: 'import' });
    expect(after.runsByLane.lane1).toEqual(['known', 'appeared']);
    // A snapshot says what exists and how it ended, but carries no prompt or directory, so the run
    // it discovered is queued for its own DTO — the client never depends on the lane's run list
    // having been generated after this snapshot (see `reconnect.test.ts`).
    expect(after.unknownRuns).toEqual(['appeared']);

    // repeating the same snapshot changes nothing
    const before = useMissionStore.getState().runs.known;
    s.reconcileLane('lane1', [
      {
        laneId: 'lane1',
        runId: 'known',
        origin: 'execution',
        status: 'finished',
        startedAt: 10,
        endedAt: 80,
      },
    ]);
    expect(useMissionStore.getState().runsByLane.lane1).toEqual(['known', 'appeared']);
    expect(useMissionStore.getState().runs.known?.status).toBe(before?.status);
  });

  it('reports last seq per run for socket resume, including runs with no events yet', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('r'), dto('empty', { startedAt: 20 })]);
    s.ingest(loadFixture('flat', { laneId: 'lane1', runId: 'r' }).slice(0, 12));
    // `empty` is a run the client knows but holds no events for (hydrated from `runs.list`, or
    // first seen as a lifecycle): a reconnect has to backfill it from seq 1, so it is sent as 0.
    expect(s.lastSeqByRun()).toEqual({ r: 12, empty: 0 });
  });

  it('records how long a run has had a gap, and clears it when the gap fills', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('r')]);
    const envs = loadFixture('flat', { laneId: 'lane1', runId: 'r' });
    s.ingest(envs.slice(5, 10)); // seq 6..10: nothing can be applied yet
    const since = useMissionStore.getState().pendingSince.r;
    expect(since).toBeGreaterThan(0);
    s.ingest(envs.slice(6, 11)); // more of the same gap: the first sighting is what ages
    expect(useMissionStore.getState().pendingSince.r).toBe(since);
    s.ingest(envs.slice(0, 5)); // seq 1..5 fill it
    expect(useMissionStore.getState().pendingSince.r).toBeUndefined();
    expect(useMissionStore.getState().pending.r).toBeUndefined();
  });

  it('closing a lane takes its runs and their bookkeeping with it', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane, { ...lane, id: 'lane2', name: 'Lane 2', createdAt: 2 }]);
    s.hydrateRuns([dto('r'), dto('other', { laneId: 'lane2' })]);
    const envs = loadFixture('flat', { laneId: 'lane1', runId: 'r' });
    s.ingest(envs.slice(0, 3));
    s.ingest(envs.slice(5, 10)); // leaves a gap, so `pending`/`pendingSince` have entries
    s.ingest(loadFixture('flat', { laneId: 'lane1', runId: 'ghost' }).slice(0, 2)); // unknown run
    s.setActiveCall('lane1', { runId: 'r', callId: 'call-a' });
    s.select('lane1', { runId: 'r', callId: 'call-a' });
    s.rememberLaneScroll('lane1', { offset: 420, following: false });
    s.showPane('lane1', { kind: 'outputs', path: 'HANDOFF.md' });
    s.markEventsFailed('r');
    expect(useMissionStore.getState().unknownRuns).toEqual(['ghost']);

    s.removeLane('lane1');
    const after = useMissionStore.getState();
    expect(after.lanes.lane1).toBeUndefined();
    expect(after.laneOrder).toEqual(['lane2']);
    expect(after.runsByLane.lane1).toBeUndefined();
    expect(after.runs.r).toBeUndefined();
    expect(after.runs.ghost).toBeUndefined();
    expect(after.pending.r).toBeUndefined();
    expect(after.pendingSince.r).toBeUndefined();
    expect(after.eventsLoaded.r).toBeUndefined();
    expect(after.eventsFailed.r).toBeUndefined();
    expect(after.activeCallByLane.lane1).toBeUndefined();
    expect(after.laneScrollByLane.lane1).toBeUndefined();
    expect(after.paneByLane.lane1).toBeUndefined();
    expect(after.unknownRuns).toEqual([]);
    expect(after.selection).toEqual({ laneId: null, call: null });
    // the other lane is untouched
    expect(after.runs.other?.runId).toBe('other');
    expect(after.runsByLane.lane2).toEqual(['other']);
  });

  it('keeps each lane’s reading position on its own, so remounting a trajectory can restore it', () => {
    // The position is held here, outside the component, because `LaneRuns` is unmounted every time
    // the session view shows a delegate or the outputs — and a cleanup cannot read it back off the
    // DOM at that point (React has already detached the ref, and the node reports `scrollTop: 0`).
    const s = useMissionStore.getState();
    s.hydrateLanes([lane, { ...lane, id: 'lane2', name: 'Lane 2', createdAt: 2 }]);
    expect(useMissionStore.getState().laneScrollByLane.lane1).toBeUndefined();

    s.rememberLaneScroll('lane1', { offset: 420, following: false });
    expect(useMissionStore.getState().laneScrollByLane.lane1).toEqual({ offset: 420, following: false });
    // One lane's position is not another's.
    expect(useMissionStore.getState().laneScrollByLane.lane2).toBeUndefined();

    // Returning to the tail is a position too, and overwrites the earlier one.
    s.rememberLaneScroll('lane1', { offset: 1082, following: true });
    expect(useMissionStore.getState().laneScrollByLane.lane1).toEqual({ offset: 1082, following: true });
  });

  it('treats two Outputs requests for different files as different panes', () => {
    // A link inside the agent's own handoff index opens the file it names; asking for a second file
    // must move the preview rather than being discarded as "already on Outputs".
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.showPane('lane1', { kind: 'outputs', path: 'HANDOFF.md' });
    expect(useMissionStore.getState().paneByLane.lane1).toEqual({ kind: 'outputs', path: 'HANDOFF.md' });
    s.showPane('lane1', { kind: 'outputs', path: 'proposals/plumbing.md' });
    expect(useMissionStore.getState().paneByLane.lane1).toEqual({
      kind: 'outputs',
      path: 'proposals/plumbing.md',
    });
    // Opening a delegate closes the inspector, which described a card in the body being replaced.
    s.select('lane1', { runId: 'r', callId: 'call-a' });
    s.showPane('lane1', { kind: 'agent', key: 'r~call-a' });
    expect(useMissionStore.getState().selection).toEqual({ laneId: 'lane1', call: null });
  });

  it('records a run whose events could not be loaded, and only that run', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('a'), dto('b', { startedAt: 20 })]);
    expect(useMissionStore.getState().eventsFailed).toEqual({});

    s.markEventsFailed('a');
    expect(useMissionStore.getState().eventsFailed).toEqual({ a: true });
    // idempotent: `useLaneHydration` gives up once per run, but a remount may ask again
    s.markEventsFailed('a');
    expect(useMissionStore.getState().eventsFailed).toEqual({ a: true });
    // and the run itself is still there to render the message under
    expect(useMissionStore.getState().runs.a).toBeDefined();

    s.reset();
    expect(useMissionStore.getState().eventsFailed).toEqual({});
  });

  /**
   * Giving up on a fetch is not a permanent verdict: the interruption that exhausted the retries is
   * usually the one that closed the socket, so a recovered socket clears the flag (`SocketBridge`
   * calls this on open) and lets the lane try again. Only the failures are cleared — a run that
   * loaded fine is not re-fetched.
   */
  it('lets a recovered socket retry the fetches it gave up on', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('a'), dto('b', { startedAt: 20 })]);
    s.markEventsLoaded('a');
    s.markEventsLoaded('b');
    s.markEventsFailed('a');

    s.retryFailedEvents();

    const after = useMissionStore.getState();
    expect(after.eventsFailed).toEqual({});
    // `a` is eligible for hydration again; `b`, which succeeded, is not asked for a second time
    expect(after.eventsLoaded.a).toBeUndefined();
    expect(after.eventsLoaded.b).toBe(true);
    // and with nothing failing it is a no-op
    s.retryFailedEvents();
    expect(useMissionStore.getState().eventsLoaded.b).toBe(true);
  });

  it('forgets an unknown run the server has never heard of', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.ingest(loadFixture('flat', { laneId: 'lane1', runId: 'ghost' }).slice(0, 2));
    expect(useMissionStore.getState().unknownRuns).toEqual(['ghost']);
    s.forgetUnknownRun('ghost');
    expect(useMissionStore.getState().unknownRuns).toEqual([]);
    // the run's own view stays: the events arrived and are worth showing
    expect(useMissionStore.getState().runs.ghost).toBeDefined();
  });

  it('carries an outline jump to LaneRuns and keeps the group-open request past it', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    expect(useMissionStore.getState().jumpRequest).toBeNull();

    s.requestJump('lane1', { runId: 'r1', callId: 'call-a' });
    const first = useMissionStore.getState().jumpRequest;
    expect(first).toMatchObject({ laneId: 'lane1', call: { runId: 'r1', callId: 'call-a' } });

    // A stale nonce must not clear a newer request (LaneRuns clears the one it served).
    s.requestJump('lane1', { runId: 'r1', callId: 'call-b' });
    const second = useMissionStore.getState().jumpRequest;
    expect(second?.nonce).toBeGreaterThan(first?.nonce ?? 0);
    s.clearJump(first?.nonce ?? 0);
    expect(useMissionStore.getState().jumpRequest).toBe(second);
    s.clearJump(second?.nonce ?? 0);
    expect(useMissionStore.getState().jumpRequest).toBeNull();

    // The group request outlives the jump: the SubagentGroup that has to unfold usually mounts only
    // once the jump has scrolled its row into the window.
    const parent = { runId: 'r1', callId: 'call-parent' };
    s.openGroupFor(parent);
    expect(useMissionStore.getState().groupOpenRequests[callKey(parent)]).toBe(1);
    s.openGroupFor(parent);
    expect(useMissionStore.getState().groupOpenRequests[callKey(parent)]).toBe(2);
    expect(
      useMissionStore.getState().groupOpenRequests[callKey({ runId: 'r1', callId: 'call-other' })],
    ).toBeUndefined();
    // R7: the same call id in a *replay* of that run is a different group, and stays folded
    const replayed = { runId: 'replay-of-r1', callId: 'call-parent' };
    expect(useMissionStore.getState().groupOpenRequests[callKey(replayed)]).toBeUndefined();
    s.settleGroupOpen([parent]);
    expect(useMissionStore.getState().groupOpenRequests[callKey(parent)]).toBeUndefined();
  });

  it('tracks the active and selected call per run, not per call id', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    const original = { runId: 'r1', callId: 'toolu_shared' };
    const replay = { runId: 'replay-of-r1', callId: 'toolu_shared' };
    s.setActiveCall('lane1', original);
    expect(useMissionStore.getState().activeCallByLane.lane1).toEqual(original);
    // the same call id in another run is a real change, and must not be swallowed as a no-op
    s.setActiveCall('lane1', replay);
    expect(useMissionStore.getState().activeCallByLane.lane1).toEqual(replay);
    s.select('lane1', replay);
    expect(useMissionStore.getState().selection).toEqual({ laneId: 'lane1', call: replay });
  });

  it('selectLaneRuns is shallow-equal across calls, so it must be wrapped in useShallow', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.hydrateRuns([dto('a', { startedAt: 10 }), dto('b', { startedAt: 20 })]);
    const state = useMissionStore.getState();
    const first = selectLaneRuns('lane1')(state);
    const second = selectLaneRuns('lane1')(state);
    expect(first).not.toBe(second); // fresh array each call...
    expect(first).toHaveLength(second.length);
    first.forEach((run, i) => {
      expect(second[i]).toBe(run); // ...but same elements: shallow-equal
    });
  });
});

describe('project workspace columns', () => {
  beforeEach(() => useMissionStore.getState().reset());

  it('removes saved selections that were archived before this browser loaded', () => {
    const s = useMissionStore.getState();
    s.setWorkspaceSessions(lane.projectRoot, ['gone', lane.id]);
    s.setWorkspaceSessions('/another-project', ['other']);
    s.hydrateLanes([lane], { kind: 'project', root: lane.projectRoot });
    expect(useMissionStore.getState().workspaceByProject[lane.projectRoot]).toEqual([lane.id]);
    expect(useMissionStore.getState().workspaceByProject['/another-project']).toEqual(['other']);
  });

  it('adds, removes, and drops the oldest choice at the cap', () => {
    const s = useMissionStore.getState();
    const root = '/tmp/x';
    for (const id of ['a', 'b', 'c']) s.toggleWorkspaceSession(root, id, 3);
    expect(useMissionStore.getState().workspaceByProject[root]).toEqual(['a', 'b', 'c']);
    // A fourth is what the operator wants, so it arrives and the one looked at longest ago goes.
    s.toggleWorkspaceSession(root, 'd', 3);
    expect(useMissionStore.getState().workspaceByProject[root]).toEqual(['b', 'c', 'd']);
    s.toggleWorkspaceSession(root, 'c', 3);
    expect(useMissionStore.getState().workspaceByProject[root]).toEqual(['b', 'd']);
    // Emptying is allowed: the workspace shows its own empty state rather than re-adding something.
    for (const id of ['b', 'd']) s.toggleWorkspaceSession(root, id, 3);
    expect(useMissionStore.getState().workspaceByProject[root]).toEqual([]);
  });

  it('keeps each project’s columns to itself', () => {
    const s = useMissionStore.getState();
    s.toggleWorkspaceSession('/a', 'lane1', 3);
    s.toggleWorkspaceSession('/b', 'lane2', 3);
    expect(useMissionStore.getState().workspaceByProject).toEqual({ '/a': ['lane1'], '/b': ['lane2'] });
  });

  it('drops a session archived elsewhere from every project’s columns', () => {
    const s = useMissionStore.getState();
    s.hydrateLanes([lane]);
    s.toggleWorkspaceSession('/tmp/x', 'lane1', 3);
    s.toggleWorkspaceSession('/tmp/x', 'other', 3);
    s.removeLane('lane1');
    expect(useMissionStore.getState().workspaceByProject['/tmp/x']).toEqual(['other']);
  });

  it('setWorkspaceSessions is a no-op when nothing changes, so subscribers do not churn', () => {
    const s = useMissionStore.getState();
    s.setWorkspaceSessions('/a', ['lane1']);
    const before = useMissionStore.getState().workspaceByProject;
    s.setWorkspaceSessions('/a', ['lane1']);
    expect(useMissionStore.getState().workspaceByProject).toBe(before);
  });
});
