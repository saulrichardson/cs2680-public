'use client';
import { ChevronLeft, Globe } from 'lucide-react';
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { agentPath } from '@/core/agents';
import { deriveOutline } from '@/core/derive';
import { getCall } from '@/core/reducer';
import type { CallRef } from '@/core/ref';
import type { AgentGraph, AgentNode, Block, RunView } from '@/core/types';
import { BlockRow } from '@/ui/components/blocks/BlockRow';
import { ResultFold } from '@/ui/components/blocks/ResultFold';
import { Outline, type OutlineGroup } from '@/ui/components/outline/Outline';
import { formatDuration, formatTokens } from '@/ui/format';
import { CONVERSATION, useMissionStore } from '@/ui/store/missionStore';
import { callElementId } from '@/ui/store/rows';
import { AgentChildren } from './AgentMap';

/**
 * One delegate's own work, in the session it belongs to.
 *
 * Selecting an agent in the navigation replaces the conversation with
 * the delegate's trajectory and report. Agent cards preserve parent context while the tool-call
 * outline stays alongside the dialogue. The assigned task is available in a disclosure.
 *
 * Three things it deliberately does not do. It renders no composer and no stop button for the
 * child, because a native subagent has no separate session to talk to or cancel and offering either
 * would be a control that does nothing. It shows no cost or turn count, because the CLI publishes
 * neither per task. And it never invents a trajectory: with `--forward-subagent-text` off there are
 * no forwarded blocks, and it says so instead of leaving an empty panel that reads like a delegate
 * that did nothing — the brief, the reported figures and the report are all still here.
 *
 * It renders alongside the outline and *instead of* `LaneRuns`, never as well as: both build tool
 * card DOM ids from run and call, so mounting both would duplicate ids and send a jump to whichever
 * came first.
 */
export function AgentPanel({
  laneId,
  node,
  run,
  graph,
  navigation,
  onNavigate,
}: {
  laneId: string;
  node: AgentNode;
  run: RunView;
  graph: AgentGraph;
  navigation(steps: ReactNode): ReactNode;
  onNavigate(): void;
}) {
  const showPane = useMissionStore((s) => s.showPane);
  const selected = useMissionStore((s) => s.selection.call);
  const scroller = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const report = useRef<HTMLElement>(null);
  // How much of a long forwarded trajectory is mounted (see `CHILD_BLOCK_WINDOW`). Held here, not
  // in `ChildBlocks`, because an outline jump to a call past the window has to widen it first —
  // otherwise the row it wants to scroll to is not in the document and the click does nothing.
  const [limit, setLimit] = useState(CHILD_BLOCK_WINDOW);

  const groups = useMemo<OutlineGroup[]>(
    () => [
      {
        key: node.key,
        runId: run.runId,
        label: node.title,
        status: run.status,
        // The delegate's own calls, from its own forwarded blocks — not the whole run's.
        items: deriveOutline({ ...run, blocks: node.blocks }, run.cwd),
      },
    ],
    [node.key, node.title, node.blocks, run],
  );

  /**
   * Scrolling inside this panel, rather than through the lane's virtualizer.
   *
   * The panel is a plain scroller — a delegate's forwarded trajectory is bounded by what one task
   * did — so the card is in the document and `scrollIntoView` reaches it. A call nested inside a
   * further `SubagentGroup` still has to be unfolded first, which is the same request the lane
   * makes; the group picks it up on its next render.
   */
  const jump = useCallback(
    (ref: CallRef) => {
      useMissionStore.getState().select(laneId, ref);
      // The target may be a top-level step past the mounted window, or nested inside one. Either
      // way, widening to cover its position is what makes the scroll below able to find it.
      const index = node.blocks.findIndex(
        (b) => b.kind === 'tool' && (b.callId === ref.callId || topLevelHolds(run, b.callId, ref.callId)),
      );
      if (index >= 0) setLimit((n) => (index < n ? n : index + 1));
      const call = getCall(run, ref.callId);
      for (let a = call?.parentToolUseId ?? null; a; a = getCall(run, a)?.parentToolUseId ?? null)
        useMissionStore.getState().openGroupFor({ runId: run.runId, callId: a });
      const scrollTo = () => document.getElementById(callElementId(ref))?.scrollIntoView({ block: 'center' });
      requestAnimationFrame(scrollTo);
      setTimeout(scrollTo, 120);
      onNavigate();
    },
    [laneId, run, node.blocks, onNavigate],
  );

  // Moving the reading position to the top of the delegate the operator just opened, once, on the
  // switch. Not on every event: a node that reported progress while they were reading must not
  // scroll the panel back to the top or take the focus ring off whatever they were using.
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on node.key so this runs on a change of delegate, not on the node object changing as its task reports.
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
    heading.current?.focus({ preventScroll: true });
    setLimit(CHILD_BLOCK_WINDOW);
  }, [node.key]);

  const trail = agentPath(graph, node.key);
  const reported = [
    node.reported.durationMs !== undefined ? formatDuration(node.reported.durationMs) : undefined,
    node.reported.toolUses !== undefined ? `${node.reported.toolUses} tool calls` : undefined,
    node.reported.totalTokens !== undefined ? `${formatTokens(node.reported.totalTokens)} tokens` : undefined,
  ].filter((p): p is string => p !== undefined);

  return (
    <>
      {navigation(
        <Outline groups={groups} variant="sidebar" active={null} selected={selected} onJump={jump} />,
      )}
      <section className="agent-panel" data-testid="agent-panel" data-agent-key={node.key} ref={scroller}>
        <header className="agent-panel-head">
          <nav className="agent-trail" aria-label="Where this delegate sits">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => showPane(laneId, CONVERSATION)}
              data-testid="agent-panel-back"
            >
              <ChevronLeft size={13} /> Conversation
            </button>
            {trail.slice(0, -1).map((ancestor) => (
              <button
                key={ancestor.key}
                type="button"
                className="agent-trail-step truncate"
                onClick={() => showPane(laneId, { kind: 'agent', key: ancestor.key })}
              >
                {ancestor.title}
              </button>
            ))}
          </nav>
          {/* Focusable so opening a delegate moves the reading position here for a screen reader,
              and so the heading is where focus lands rather than the first tool card. */}
          <h2 className="agent-panel-title" tabIndex={-1} ref={heading} data-testid="agent-panel-title">
            {node.title}
          </h2>
          <div className="agent-panel-facts faint">
            {node.subagentType && <span className="tag">{node.subagentType}</span>}
            <span data-testid="agent-panel-state">{node.state}</span>
            {node.unresolved && (
              <span className="caution" data-testid="agent-panel-unresolved">
                the run ended before this delegate reported an outcome
              </span>
            )}
            {reported.length > 0 && <span className="mono">{reported.join(' · ')}</span>}
            {node.browserCalls > 0 && (
              <span className="pill pill-browser" data-testid="agent-panel-browser">
                <Globe size={11} /> {node.browserCalls} browser call{node.browserCalls === 1 ? '' : 's'}
              </span>
            )}
            {node.report && (
              <button
                type="button"
                className="agent-report-link"
                onClick={() => report.current?.scrollIntoView({ block: 'start' })}
              >
                View report
              </button>
            )}
          </div>
        </header>

        <AgentChildren laneId={laneId} node={node} graph={graph} />
        {node.assignment && (
          <details key={node.key} className="agent-assignment" data-testid="agent-assignment">
            <summary>Assigned task</summary>
            <ResultFold text={node.assignment} isError={false} patches={[]} cwd={run.cwd} />
          </details>
        )}

        <section className="agent-section">
          <h3>Activity</h3>
          {node.blocks.length > 0 ? (
            <ChildBlocks blocks={node.blocks} run={run} limit={limit} setLimit={setLimit} />
          ) : (
            <p className="faint" data-testid="agent-no-forward">
              This delegate's own steps were not forwarded to the stream, so there is no trajectory to show.
              Its brief and its report are still here, and the figures above are the ones its task reported.
            </p>
          )}
          {node.activity ? (
            <p className="muted" data-testid="agent-panel-activity">
              Currently: {node.activity}
              {node.lastToolName ? ` · last tool ${node.lastToolName}` : ''}
            </p>
          ) : (
            node.unresolved &&
            node.lastActivity && (
              <p className="muted" data-testid="agent-panel-last-activity">
                Last reported activity: {node.lastActivity}
                {node.lastToolName ? ` · last tool ${node.lastToolName}` : ''}
              </p>
            )
          )}
        </section>

        <section className="agent-section" ref={report}>
          <h3>Report</h3>
          {node.report ? (
            <div data-testid="agent-report">
              <ResultFold
                text={node.report}
                markdown={node.reportIsMarkdown}
                isError={node.state === 'failed'}
                patches={[]}
                cwd={run.cwd}
              />
            </div>
          ) : (
            <p className="faint" data-testid="agent-no-report">
              {node.unresolved ? 'No report: the run ended before this delegate finished.' : 'No report yet.'}
            </p>
          )}
        </section>
      </section>
    </>
  );
}

/** The delegate's forwarded blocks, through exactly the renderer the conversation uses. */
/** Is `target` this top-level call, or nested somewhere under it? Bounded by the run's own tree. */
function topLevelHolds(run: RunView, topLevelCallId: string, target: string): boolean {
  for (let id: string | null = target; id; id = getCall(run, id)?.parentToolUseId ?? null) {
    if (id === topLevelCallId) return true;
  }
  return false;
}

/**
 * How many of a delegate's forwarded blocks are mounted before the panel asks.
 *
 * Nothing bounds a subagent's trajectory: the recorded survey forwards eleven tool calls, and a
 * research delegate that reads forty pages forwards hundreds of heavy cards — diffs, folded
 * results, nested groups. `LaneRuns` virtualizes for exactly this reason and holds its mounted rows
 * under ~120; a panel that mounted the lot would blow that budget for the one view whose job is to
 * make a child's work readable. So the window matches the budget rather than inventing a new one,
 * and growing it is the operator's decision, in steps of the same size.
 */
const CHILD_BLOCK_WINDOW = 120;

function ChildBlocks({
  blocks,
  run,
  limit,
  setLimit,
}: {
  blocks: Block[];
  run: RunView;
  limit: number;
  setLimit(next: (n: number) => number): void;
}) {
  const shown = blocks.length <= limit ? blocks : blocks.slice(0, limit);
  const hidden = blocks.length - shown.length;
  return (
    <div className="agent-blocks">
      {shown.map((b) => (
        <div className="row row-block" key={b.kind === 'tool' ? `tool:${b.callId}` : `${b.kind}:${b.id}`}>
          <BlockRow block={b} run={run} depth={0} siblings={blocks} />
        </div>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="btn agent-more"
          onClick={() => setLimit((n) => n + CHILD_BLOCK_WINDOW)}
          data-testid="agent-more-blocks"
        >
          Show {Math.min(hidden, CHILD_BLOCK_WINDOW)} more of {blocks.length} steps
        </button>
      )}
    </div>
  );
}
