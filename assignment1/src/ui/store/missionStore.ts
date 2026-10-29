'use client';
import { create } from 'zustand';
import { useShallow } from 'zustand/react/shallow';
import { applyEvent, createRunView, applyLifecycle as reduceLifecycle } from '@/core/reducer';
import type { Envelope, LaneDto, RunLifecycle, RunSummaryDto, RunView } from '@/core/types';
import { type CallRef, callKey, sameCall } from './rows';

type Connection = 'connecting' | 'open' | 'closed';

/**
 * What a session view's main body is showing.
 *
 * A closed union rather than a pair of booleans because the three are genuinely exclusive, and the
 * one invariant that matters follows from that: exactly one trajectory is mounted at a time. The
 * parent conversation and a delegate's own trajectory render the same `BlockRow` components, which
 * give every tool card a DOM id built from its run and call — so mounting both would put two
 * elements with the same id in the document and send the outline's scroll to whichever came first.
 */
export type SessionPane =
  | { kind: 'conversation' }
  | { kind: 'agent'; key: string }
  /**
   * `path` is how a link inside the agent's own markdown opens the file it names: a handoff index
   * saying `[Plumbing](proposals/plumbing.md)` has to land on that document, not merely on the
   * Outputs tab. Absent means "the pane, wherever it was left".
   */
  | { kind: 'outputs'; path?: string }
  /**
   * What a wrap-up session was given: its sources, and the package captured from them.
   *
   * A pane rather than a banner because it is a body's worth of content — every source, its runs,
   * and every copied file with its hash — and because it is not what an operator wants to look at
   * most of the time. Sessions that are not wrap-ups never offer it; `SessionView` falls back to
   * the conversation if a stored pane names it for a session that has no package.
   */
  | { kind: 'inputs' };

export const CONVERSATION: SessionPane = { kind: 'conversation' };

/** What a `hydrateLanes` answer covered: every session, or one project's. */
export type LaneScope = { kind: 'all' } | { kind: 'project'; root: string };

function samePane(a: SessionPane, b: SessionPane): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === 'agent' && b.kind === 'agent') return a.key === b.key;
  // Two requests for Outputs naming different files are different requests: the second must move
  // the preview, not be discarded as a no-op.
  if (a.kind === 'outputs' && b.kind === 'outputs') return a.path === b.path;
  return true;
}

export interface MissionState {
  connection: Connection;
  lanes: Record<string, LaneDto>;
  laneOrder: string[];
  runs: Record<string, RunView>;
  runsByLane: Record<string, string[]>;
  pending: Record<string, Envelope[]>;
  /**
   * When each run's buffered gap was first noticed, so `SocketBridge` can ask the hub to resend a
   * gap that never filled (a dropped frame leaves envelopes in `pending` forever otherwise).
   */
  pendingSince: Record<string, number>;
  unknownRuns: string[];
  eventsLoaded: Record<string, boolean>;
  /**
   * Runs whose stored events could not be fetched, after `useLaneHydration` exhausted its retries.
   * Present only for the failures, so the common case costs nothing to check. The trajectory is
   * blank for these runs and the lane says so rather than looking like a run that did nothing.
   */
  eventsFailed: Record<string, true>;
  /** The call the inspector is showing, identified by run *and* call (see `CallRef`). */
  selection: { laneId: string | null; call: CallRef | null };
  /**
   * What the session view's main body is showing, per lane.
   *
   * Per lane rather than globally, so moving between sessions does not carry one session's child
   * into another's view. Only ever set by something the operator did: a delegate appearing is not a
   * reason to open it, or a session with three delegates would throw the reader out of whatever
   * they were reading, three times, while the run was still going.
   */
  paneByLane: Record<string, SessionPane>;
  /** The call nearest the top of each lane's viewport, which the outline highlights. */
  activeCallByLane: Record<string, CallRef | null>;
  /**
   * Where the operator had scrolled each lane's trajectory, and whether it was following the tail.
   *
   * Kept outside the component because the component goes away: opening a delegate unmounts
   * `LaneRuns` (two trajectories must never be mounted at once — they would duplicate the tool
   * cards' DOM ids), and a fresh mount starts at the bottom, following. Somebody who had scrolled up
   * to read the evidence a delegate was spawned from lost their place every time they looked at one.
   */
  laneScrollByLane: Record<string, { offset: number; following: boolean }>;
  /**
   * What the operator has typed but not sent, per session.
   *
   * Outside the composer because the composer unmounts: opening a delegate, switching session and
   * coming back, or following a file link all threw away half-written prompts. Keyed by session so
   * one conversation's draft can never appear in another's box.
   */
  draftByLane: Record<string, string>;
  /**
   * The session the operator last opened in each project.
   *
   * Set only by explicit navigation, never by an arriving event: coming back to a project should
   * land on the conversation that was being read, not on whichever one happens to be busiest.
   * Used only when that session is still part of the project's authoritative current list.
   */
  lastSessionByProject: Record<string, string>;
  /**
   * The sessions the operator has put side by side in each project's workspace.
   *
   * Per project, so moving away and back finds the columns as they were left and never inherits
   * another project's. Held here rather than in the page because opening a session and coming back
   * is the usual way to leave the workspace. ProjectView saves this preference in browser storage;
   * a project with no entry opens on its most current session.
   */
  workspaceByProject: Record<string, string[]>;
  /**
   * A pending "scroll this lane to that call". `LaneRuns` owns the scroll — it is the only thing
   * that knows the virtualizer, and the target row is very often not in the DOM for
   * `getElementById` to find: virtualized out of the window, or folded inside a subagent group.
   * The nonce makes two jumps to the same call distinguishable.
   */
  jumpRequest: { laneId: string; call: CallRef; nonce: number } | null;
  /**
   * Nonce per tool call whose `SubagentGroup` has been asked to unfold, keyed by `callKey` so a
   * replay of the same recording does not unfold the original's groups. Kept rather than cleared:
   * the group that must open often has not mounted yet when the request is made, so it reads this
   * on mount. A number (not an object) so the group can subscribe without `useShallow`.
   */
  groupOpenRequests: Record<string, number>;

  reset(): void;
  setConnection(c: Connection): void;
  /**
   * Adopt the server's list of sessions, and drop the ones it no longer lists.
   *
   * `scope` says what the answer covered. A project page asks about one project, and a session
   * missing from *that* answer has been archived or removed; a session belonging to another
   * project is simply not what was asked about and must survive. Without this distinction the
   * first project-scoped load would wipe every other project from the store — and without the
   * removal at all, a session archived in another tab stayed behind as an editable ghost with a
   * `running` run nothing would ever finish.
   */
  hydrateLanes(lanes: LaneDto[], scope?: LaneScope): void;
  upsertLane(lane: LaneDto): void;
  removeLane(laneId: string): void;
  /** Draft prompt text, kept per session so navigating away does not lose it. */
  setDraft(laneId: string, text: string): void;
  /** Remember that this session is the one being read in its project. */
  selectSession(projectRoot: string, laneId: string): void;
  /**
   * Add or remove a session from a project's workspace columns; at the cap the oldest choice goes.
   *
   * A cap rather than a refusal: choosing a fourth session means wanting it. Removing the last
   * column is allowed — the workspace shows its empty state rather than re-adding something.
   */
  toggleWorkspaceSession(projectRoot: string, laneId: string, max: number): void;
  /** Replace a project's columns outright — what the narrow-screen switcher and a reset use. */
  setWorkspaceSessions(projectRoot: string, laneIds: string[]): void;
  /** Stop asking the server about a run it says it has never heard of. */
  forgetUnknownRun(runId: string): void;
  hydrateRuns(runs: RunSummaryDto[]): void;
  markEventsLoaded(runId: string): void;
  /** The events for this run could not be loaded and will not be retried. */
  markEventsFailed(runId: string): void;
  /**
   * A recovered socket is a reason to try the failed fetches again: the usual cause of giving up is
   * the same interruption that closed the socket. Clearing both flags lets `useLaneHydration` pick
   * those runs up again; runs that loaded fine are untouched, so nothing is re-fetched needlessly.
   */
  retryFailedEvents(): void;
  ingest(envelopes: Envelope[]): void;
  applyLifecycle(lc: RunLifecycle): void;
  /**
   * Adopt the server's whole picture of a lane: which runs exist, and what state each is in. Sent
   * on every subscribe, so this is what a reconnect is put right by.
   */
  reconcileLane(laneId: string, lifecycles: RunLifecycle[]): void;
  select(laneId: string | null, call: CallRef | null): void;
  /** Show one of the session's panes: its conversation, one delegate, or its outputs. */
  showPane(laneId: string, pane: SessionPane): void;
  setActiveCall(laneId: string, call: CallRef | null): void;
  /** Remember where a lane's trajectory was left, so remounting it returns to the same place. */
  rememberLaneScroll(laneId: string, at: { offset: number; following: boolean }): void;
  requestJump(laneId: string, call: CallRef): void;
  clearJump(nonce: number): void;
  openGroupFor(ref: CallRef): void;
  /** Drop served open requests so a remounted group returns to its own fold state. */
  settleGroupOpen(refs: CallRef[]): void;
  lastSeqByRun(): Record<string, number>;
}

const initial = (): Pick<
  MissionState,
  | 'connection'
  | 'lanes'
  | 'laneOrder'
  | 'runs'
  | 'runsByLane'
  | 'pending'
  | 'pendingSince'
  | 'unknownRuns'
  | 'eventsLoaded'
  | 'eventsFailed'
  | 'selection'
  | 'paneByLane'
  | 'activeCallByLane'
  | 'laneScrollByLane'
  | 'draftByLane'
  | 'lastSessionByProject'
  | 'workspaceByProject'
  | 'jumpRequest'
  | 'groupOpenRequests'
> => ({
  connection: 'connecting',
  lanes: {},
  laneOrder: [],
  runs: {},
  runsByLane: {},
  pending: {},
  pendingSince: {},
  unknownRuns: [],
  eventsLoaded: {},
  eventsFailed: {},
  selection: { laneId: null, call: null },
  paneByLane: {},
  activeCallByLane: {},
  laneScrollByLane: {},
  draftByLane: {},
  lastSessionByProject: {},
  workspaceByProject: {},
  jumpRequest: null,
  groupOpenRequests: {},
});

/**
 * The empty column list, shared.
 *
 * A selector answering `?? []` would allocate a new array on every snapshot read, which is exactly
 * the unstable-selector mistake the project already has a rule about: zustand compares snapshots by
 * reference, so a fresh array each time makes React re-render for ever. One frozen constant instead.
 */
export const NO_WORKSPACE_SESSIONS: readonly string[] = Object.freeze([]);

let jumpNonce = 0;

function sortLaneRuns(runs: Record<string, RunView>, ids: string[]): string[] {
  return [...new Set(ids)].sort(
    (a, b) => (runs[a]?.startedAt ?? 0) - (runs[b]?.startedAt ?? 0) || a.localeCompare(b),
  );
}

/**
 * Fold one server lifecycle into the run map in place, creating a placeholder for a run this client
 * has never seen. Mutates the copies its callers made, so that reconciling a whole lane is one
 * store update rather than one per run. The reducer's own guard keeps a terminal run terminal.
 *
 * `unknownRuns` is how a placeholder gets the rest of itself — prompt, directory, provenance — by
 * asking the server about that one run. Every path that can invent a placeholder records it, the
 * lane snapshot included.
 *
 * The snapshot used to pass `null` here, on the grounds that `SocketBridge` refreshes the whole
 * lane's run list on the same reconnect. That made the client's metadata depend on two requests
 * finishing in a particular order: a `runs.list` generated *before* a run existed, arriving *after*
 * the snapshot that names it, left a permanent placeholder with no prompt and no directory. The
 * authority contract is instead stated directly — a run this client cannot describe is one it asks
 * about — and `useUnknownRuns` asks once per run rather than once per store update.
 */
function mergeLifecycle(runs: Record<string, RunView>, unknownRuns: string[] | null, lc: RunLifecycle): void {
  const existing = runs[lc.runId];
  const view =
    existing ??
    createRunView({
      runId: lc.runId,
      laneId: lc.laneId,
      prompt: '',
      cwd: '',
      startedAt: lc.startedAt,
      status: lc.status,
      origin: lc.origin,
    });
  runs[lc.runId] = reduceLifecycle(view, lc);
  if (!existing && unknownRuns && !unknownRuns.includes(lc.runId)) unknownRuns.push(lc.runId);
}

export const useMissionStore = create<MissionState>((set, get) => ({
  ...initial(),

  reset: () => set(initial()),
  setConnection: (connection) => set({ connection }),

  hydrateLanes: (lanes, scope = { kind: 'all' }) => {
    // What the server says exists *within the scope it was asked about*. A project-scoped answer
    // is not a global snapshot: treating one as such would delete every other project's sessions
    // from the store the moment a project page loaded.
    const present = new Set(lanes.map((l) => l.id));
    const inScope = (lane: LaneDto) =>
      scope.kind === 'all' || (scope.kind === 'project' && lane.projectRoot === scope.root);
    // Archived or removed elsewhere — another tab, or this one before a reconnect. It goes through
    // the same removal a local archive does, so its runs, panes and running indicators go with it
    // rather than lingering as a session nothing can reach.
    for (const lane of Object.values(get().lanes))
      if (inScope(lane) && !present.has(lane.id)) get().removeLane(lane.id);
    set((s) => {
      const map = { ...s.lanes };
      for (const l of lanes) map[l.id] = l;
      const order = Object.values(map)
        .sort((a, b) => a.createdAt - b.createdAt)
        .map((l) => l.id);
      // Saved IDs can be stale even before their lanes have ever entered this browser's store.
      const workspaceByProject = { ...s.workspaceByProject };
      for (const [root, ids] of Object.entries(workspaceByProject)) {
        if (scope.kind === 'project' && scope.root !== root) continue;
        const valid = ids.filter((id) => map[id]?.projectRoot === root && map[id]?.archivedAt === null);
        if (valid.length !== ids.length) workspaceByProject[root] = valid;
      }
      return { lanes: map, laneOrder: order, workspaceByProject };
    });
  },
  upsertLane: (lane) =>
    set((s) => ({
      lanes: { ...s.lanes, [lane.id]: lane },
      laneOrder: s.laneOrder.includes(lane.id) ? s.laneOrder : [...s.laneOrder, lane.id],
    })),
  // A closed lane takes its runs with it: they are unreachable from the board, and leaving them in
  // `runs`/`pending` would keep the client resuming (and re-fetching) a lane the server has closed.
  removeLane: (laneId) =>
    set((s) => {
      const lanes = { ...s.lanes };
      delete lanes[laneId];
      const gone = new Set(s.runsByLane[laneId] ?? []);
      const runs = { ...s.runs };
      const pending = { ...s.pending };
      const pendingSince = { ...s.pendingSince };
      const eventsLoaded = { ...s.eventsLoaded };
      const eventsFailed = { ...s.eventsFailed };
      const runsByLane = { ...s.runsByLane };
      const activeCallByLane = { ...s.activeCallByLane };
      const laneScrollByLane = { ...s.laneScrollByLane };
      const paneByLane = { ...s.paneByLane };
      const draftByLane = { ...s.draftByLane };
      for (const runId of gone) {
        delete runs[runId];
        delete pending[runId];
        delete pendingSince[runId];
        delete eventsLoaded[runId];
        delete eventsFailed[runId];
      }
      delete runsByLane[laneId];
      delete activeCallByLane[laneId];
      delete laneScrollByLane[laneId];
      delete paneByLane[laneId];
      delete draftByLane[laneId];
      // A session archived or removed elsewhere leaves the workspace columns too: a column showing
      // a session the server no longer lists is a column nothing can fill.
      const workspaceByProject = { ...s.workspaceByProject };
      for (const [root, ids] of Object.entries(workspaceByProject))
        if (ids.includes(laneId)) workspaceByProject[root] = ids.filter((id) => id !== laneId);
      return {
        lanes,
        workspaceByProject,
        laneOrder: s.laneOrder.filter((id) => id !== laneId),
        runs,
        runsByLane,
        pending,
        pendingSince,
        eventsLoaded,
        eventsFailed,
        activeCallByLane,
        laneScrollByLane,
        paneByLane,
        draftByLane,
        unknownRuns: s.unknownRuns.filter((id) => !gone.has(id)),
        selection: s.selection.laneId === laneId ? { laneId: null, call: null } : s.selection,
      };
    }),
  selectSession: (projectRoot, laneId) =>
    set((s) =>
      s.lastSessionByProject[projectRoot] === laneId
        ? {}
        : { lastSessionByProject: { ...s.lastSessionByProject, [projectRoot]: laneId } },
    ),

  toggleWorkspaceSession: (projectRoot, laneId, max) =>
    set((s) => {
      const current = s.workspaceByProject[projectRoot] ?? [];
      const next = current.includes(laneId)
        ? current.filter((id) => id !== laneId)
        : // Appended, then trimmed from the front: the columns read left to right in the order they
          // were chosen, and the one that goes is the one looked at longest ago.
          [...current, laneId].slice(-max);
      return { workspaceByProject: { ...s.workspaceByProject, [projectRoot]: next } };
    }),

  setWorkspaceSessions: (projectRoot, laneIds) =>
    set((s) => {
      const current = s.workspaceByProject[projectRoot];
      if (current && current.length === laneIds.length && current.every((id, i) => id === laneIds[i]))
        return {};
      return { workspaceByProject: { ...s.workspaceByProject, [projectRoot]: [...laneIds] } };
    }),

  setDraft: (laneId, text) =>
    set((s) => (s.draftByLane[laneId] === text ? {} : { draftByLane: { ...s.draftByLane, [laneId]: text } })),

  forgetUnknownRun: (runId) =>
    set((s) =>
      s.unknownRuns.includes(runId) ? { unknownRuns: s.unknownRuns.filter((id) => id !== runId) } : {},
    ),

  hydrateRuns: (dtos) =>
    set((s) => {
      const runs = { ...s.runs };
      const runsByLane = { ...s.runsByLane };
      for (const d of dtos) {
        const existing = runs[d.runId];
        const base =
          existing ??
          createRunView({
            runId: d.runId,
            laneId: d.laneId,
            prompt: d.prompt,
            cwd: d.cwd,
            startedAt: d.startedAt,
            resumedFrom: d.resumedFrom,
            replayOf: d.replayOf,
            status: d.status,
            browser: d.browser,
          });
        const merged = existing
          ? {
              ...existing,
              prompt: d.prompt || existing.prompt,
              cwd: d.cwd || existing.cwd,
              resumedFrom: d.resumedFrom ?? existing.resumedFrom,
              replayOf: d.replayOf ?? existing.replayOf,
              model: existing.model ?? d.model,
              // The row is the authority on what the run was launched with; a placeholder created
              // from a bare envelope guessed `off`, and this is where that guess is corrected.
              browser: d.browser,
            }
          : base;
        runs[d.runId] = reduceLifecycle(merged, d);
        runsByLane[d.laneId] = sortLaneRuns(runs, [...(runsByLane[d.laneId] ?? []), d.runId]);
      }
      const known = new Set(dtos.map((d) => d.runId));
      return { runs, runsByLane, unknownRuns: s.unknownRuns.filter((id) => !known.has(id)) };
    }),

  markEventsLoaded: (runId) => set((s) => ({ eventsLoaded: { ...s.eventsLoaded, [runId]: true } })),

  markEventsFailed: (runId) => set((s) => ({ eventsFailed: { ...s.eventsFailed, [runId]: true } })),

  retryFailedEvents: () =>
    set((s) => {
      const failed = Object.keys(s.eventsFailed);
      if (failed.length === 0) return {};
      const eventsLoaded = { ...s.eventsLoaded };
      for (const runId of failed) delete eventsLoaded[runId];
      return { eventsFailed: {}, eventsLoaded };
    }),

  ingest: (envelopes) =>
    set((s) => {
      if (envelopes.length === 0) return {};
      const runs = { ...s.runs };
      const pending = { ...s.pending };
      const pendingSince = { ...s.pendingSince };
      const runsByLane = { ...s.runsByLane };
      const unknown = [...s.unknownRuns];
      const byRun = new Map<string, Envelope[]>();
      for (const e of envelopes) byRun.set(e.runId, [...(byRun.get(e.runId) ?? []), e]);
      for (const [runId, incoming] of byRun) {
        let view = runs[runId];
        if (!view) {
          const first = incoming[0] as Envelope;
          view = createRunView({
            runId,
            laneId: first.laneId,
            prompt: '',
            cwd: '',
            startedAt: first.receivedAt,
          });
          runsByLane[first.laneId] = sortLaneRuns({ ...runs, [runId]: view }, [
            ...(runsByLane[first.laneId] ?? []),
            runId,
          ]);
          if (!unknown.includes(runId)) unknown.push(runId);
        }
        const queue = [...(pending[runId] ?? []), ...incoming].sort((a, b) => a.seq - b.seq);
        const rest: Envelope[] = [];
        for (const env of queue) {
          if (env.seq <= view.lastSeq) continue;
          if (env.seq === view.lastSeq + 1) view = applyEvent(view, env);
          else rest.push(env);
        }
        runs[runId] = view;
        if (rest.length) {
          pending[runId] = rest;
          // Keep the first sighting: the age of the gap is what decides whether to ask for a resend.
          pendingSince[runId] = pendingSince[runId] ?? Date.now();
        } else {
          delete pending[runId];
          delete pendingSince[runId];
        }
      }
      return { runs, pending, pendingSince, runsByLane, unknownRuns: unknown };
    }),

  applyLifecycle: (lc) =>
    set((s) => {
      const runs = { ...s.runs };
      const unknownRuns = [...s.unknownRuns];
      mergeLifecycle(runs, unknownRuns, lc);
      return {
        runs,
        runsByLane: {
          ...s.runsByLane,
          [lc.laneId]: sortLaneRuns(runs, [...(s.runsByLane[lc.laneId] ?? []), lc.runId]),
        },
        unknownRuns,
      };
    }),

  reconcileLane: (laneId, lifecycles) =>
    set((s) => {
      const runs = { ...s.runs };
      const unknownRuns = [...s.unknownRuns];
      for (const lc of lifecycles) mergeLifecycle(runs, unknownRuns, lc);
      return {
        runs,
        unknownRuns,
        runsByLane: {
          ...s.runsByLane,
          [laneId]: sortLaneRuns(runs, [
            ...(s.runsByLane[laneId] ?? []),
            ...lifecycles.map((lc) => lc.runId),
          ]),
        },
      };
    }),

  select: (laneId, call) => set({ selection: { laneId, call } }),
  showPane: (laneId, pane) =>
    set((s) =>
      samePane(s.paneByLane[laneId] ?? CONVERSATION, pane)
        ? {}
        : {
            paneByLane: { ...s.paneByLane, [laneId]: pane },
            // The body is about to be replaced, and the inspector describes a card in the body it
            // was opened from. Leaving it up would show a call from a trajectory that is no longer
            // on screen, beside a heading naming something else.
            selection: s.selection.laneId === laneId ? { laneId, call: null } : s.selection,
          },
    ),
  setActiveCall: (laneId, call) =>
    set((s) =>
      sameCall(s.activeCallByLane[laneId] ?? null, call)
        ? {}
        : { activeCallByLane: { ...s.activeCallByLane, [laneId]: call } },
    ),

  rememberLaneScroll: (laneId, at) =>
    set((s) => ({ laneScrollByLane: { ...s.laneScrollByLane, [laneId]: at } })),

  requestJump: (laneId, call) => {
    jumpNonce += 1;
    set({ jumpRequest: { laneId, call, nonce: jumpNonce } });
  },
  // Nonce-checked so a request made while the previous one was being served isn't dropped.
  clearJump: (nonce) => set((s) => (s.jumpRequest?.nonce === nonce ? { jumpRequest: null } : {})),
  openGroupFor: (ref) =>
    set((s) => {
      const key = callKey(ref);
      return { groupOpenRequests: { ...s.groupOpenRequests, [key]: (s.groupOpenRequests[key] ?? 0) + 1 } };
    }),
  settleGroupOpen: (refs) =>
    set((s) => {
      const keys = refs.map(callKey);
      if (!keys.some((k) => k in s.groupOpenRequests)) return {};
      const next = { ...s.groupOpenRequests };
      for (const k of keys) delete next[k];
      return { groupOpenRequests: next };
    }),

  // Every run the client knows about, `lastSeq: 0` included: a run we have a row for but no events
  // for (hydrated from `runs.list`, or first seen as a lifecycle) is exactly the one a reconnect
  // has to backfill from the start. The hub treats 0 as "everything from seq 1".
  lastSeqByRun: () => Object.fromEntries(Object.values(get().runs).map((r) => [r.runId, r.lastSeq])),
}));

/**
 * Returns a fresh array on every call, so it must be wrapped in `useShallow`. Components use
 * `useLaneRuns` below, which does that; this is exported only so `tests/ui/store.test.ts` can pin
 * the reference semantics that make the wrapping necessary — a thing no test can see through a hook.
 *
 * @internal test-only export
 */
export const selectLaneRuns = (laneId: string) => (s: MissionState) =>
  (s.runsByLane[laneId] ?? []).map((id) => s.runs[id]).filter((r): r is RunView => Boolean(r));
/** As `selectLaneRuns`, for a lane's running state. Reached through `useLaneStatus` below. */
const selectLaneStatus = (laneId: string) => (s: MissionState) => {
  const runs = (s.runsByLane[laneId] ?? []).map((id) => s.runs[id]);
  // `active` is the operation the lane is running right now, which is not always the newest row:
  // an import lands as a finished run *after* the agent that is still working, and Stop targeting
  // "the last run" therefore cancelled the recording and left the agent going (readiness review
  // R2). The server allows one active operation per lane, so there is at most one of these.
  const active = runs.findLast((r) => r?.status === 'running');
  return { running: active !== undefined, last: runs.at(-1), active };
};

/** Shallow-compared subscription to a lane's runs, safe to use directly in components. */
export function useLaneRuns(laneId: string): RunView[] {
  return useMissionStore(useShallow(selectLaneRuns(laneId)));
}

/** Shallow-compared subscription to a lane's running state and last run, safe to use directly in components. */
export function useLaneStatus(laneId: string): {
  running: boolean;
  last: RunView | undefined;
  /** The run this lane is executing or playing back right now — what Stop must reach. */
  active: RunView | undefined;
} {
  return useMissionStore(useShallow(selectLaneStatus(laneId)));
}
