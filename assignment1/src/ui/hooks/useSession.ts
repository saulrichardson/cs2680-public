'use client';
import { useMemo } from 'react';
import { agentNode, deriveAgentGraph } from '@/core/agents';
import { deriveBrowserView } from '@/core/browser';
import type { AgentGraph, AgentNode, BrowserView, RunView } from '@/core/types';
import {
  CONVERSATION,
  type SessionPane,
  useLaneRuns,
  useLaneStatus,
  useMissionStore,
} from '@/ui/store/missionStore';

export interface SessionState {
  runs: RunView[];
  graph: AgentGraph;
  /**
   * What the body is showing. `conversation` when the stored pane names a delegate the session no
   * longer has — a run reset, a lane rehydrated from scratch, a recording re-imported — because a
   * body that has nothing to render is worse than the conversation it came from.
   */
  pane: SessionPane;
  /** The selected delegate, when the pane names one that exists. */
  node: AgentNode | undefined;
  /** The run the selected delegate belongs to. */
  nodeRun: RunView | undefined;
  /** What the newest run can honestly say about the browser (see `deriveBrowserView`). */
  browser: BrowserView;
  /** The run that is executing or playing back right now — what Stop must reach. */
  active: RunView | undefined;
  last: RunView | undefined;
}

/**
 * Everything the session view derives from the store, in one memo.
 *
 * All of it is a projection of the lane's `RunView`s, which is what makes live execution and replay
 * identical here: a replayed recording produces the same graph, node for node, and each node
 * appears when its own `Agent` call is reached rather than at the start.
 */
export function useSession(laneId: string): SessionState {
  const runs = useLaneRuns(laneId);
  const { active, last } = useLaneStatus(laneId);
  const storedPane = useMissionStore((s) => s.paneByLane[laneId] ?? CONVERSATION);
  return useMemo(() => {
    const graph = deriveAgentGraph(runs);
    const node = storedPane.kind === 'agent' ? agentNode(graph, storedPane.key) : undefined;
    const pane = storedPane.kind === 'agent' && !node ? CONVERSATION : storedPane;
    return {
      runs,
      graph,
      pane,
      node,
      nodeRun: node ? runs.find((r) => r.runId === node.runId) : undefined,
      browser: deriveBrowserView(last ?? runs.at(-1) ?? EMPTY_RUN),
      active,
      last,
    };
  }, [runs, storedPane, active, last]);
}

/**
 * Stands in for "this lane has never run anything", so `deriveBrowserView` has a total input rather
 * than the session view carrying an `undefined` branch through every use of it.
 */
const EMPTY_RUN: RunView = {
  runId: '',
  laneId: '',
  prompt: '',
  cwd: '',
  status: 'finished',
  origin: 'execution',
  browser: 'off',
  startedAt: 0,
  blocks: [],
  callsById: {},
  context: { tokens: 0 },
  setup: { hooks: [], mcpServers: [], browserTools: [] },
  retries: 0,
  deniedCount: 0,
  unparsedCount: 0,
  ignoredCount: 0,
  eventCount: 0,
  lastSeq: 0,
};
