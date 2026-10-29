'use client';
import { ArrowLeft, ArrowRight, Bot, Check, MessagesSquare } from 'lucide-react';
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react';
import { agentNode } from '@/core/agents';
import type { AgentGraph, AgentNode, AgentNodeState } from '@/core/types';
import { CONVERSATION, type SessionPane, useMissionStore } from '@/ui/store/missionStore';

const LABEL: Record<AgentNodeState, string> = {
  launching: 'Starting',
  working: 'Working',
  paused: 'Paused',
  completed: 'Finished',
  failed: 'Failed',
  stopped: 'Stopped',
};

/** Select among siblings; deeper delegations are opened from their parent's panel. */
export function AgentMap({ laneId, graph, pane }: { laneId: string; graph: AgentGraph; pane: SessionPane }) {
  const showPane = useMissionStore((s) => s.showPane);
  const selected = pane.kind === 'agent' ? agentNode(graph, pane.key) : undefined;
  const parent = agentNode(graph, selected?.parentKey);
  const keys = parent?.childKeys ?? graph.rootKeys;
  return (
    <nav
      className="agent-map"
      aria-label="Agent navigation"
      data-testid="agent-map"
      data-nodes={graph.nodes.length}
    >
      <AgentCards
        label={parent ? `Subagents of ${parent.title}` : 'Agents'}
        selectedKey={selected?.key ?? 'main'}
      >
        <button
          type="button"
          className={`agent-card agent-card-parent${!selected ? ' selected' : ''}`}
          aria-pressed={!selected}
          data-testid="agent-node-root"
          onClick={() => showPane(laneId, parent ? { kind: 'agent', key: parent.key } : CONVERSATION)}
        >
          <span className="agent-card-top">
            {parent ? <ArrowLeft size={16} /> : <MessagesSquare size={16} />}
            <span>{parent ? 'Back to parent' : 'Main session'}</span>
            {!selected && <Check size={14} className="agent-card-check" />}
          </span>
          <span className="agent-card-name">{parent ? parent.title : 'Full conversation'}</span>
          <span className="agent-card-meta" data-testid="agent-map-tally">
            {keys.length} subagent{keys.length === 1 ? '' : 's'}
            {!parent && graph.activeCount > 0 ? ` · ${graph.activeCount} active` : ''}
          </span>
        </button>
        {keys.map((key) => {
          const node = agentNode(graph, key);
          return node ? (
            <AgentCard key={key} laneId={laneId} node={node} selected={selected?.key === key} />
          ) : null;
        })}
      </AgentCards>
    </nav>
  );
}

/** Children stay with the agent that delegated them, instead of flattening the whole graph. */
export function AgentChildren({
  laneId,
  node,
  graph,
}: {
  laneId: string;
  node: AgentNode;
  graph: AgentGraph;
}) {
  if (node.childKeys.length === 0) return null;
  return (
    <section className="agent-children" aria-label="Delegated work">
      <AgentCards label={`Delegated work · ${node.childKeys.length} subagents`}>
        {node.childKeys.map((key) => {
          const child = agentNode(graph, key);
          return child ? <AgentCard key={key} laneId={laneId} node={child} selected={false} /> : null;
        })}
      </AgentCards>
    </section>
  );
}

function AgentCard({ laneId, node, selected }: { laneId: string; node: AgentNode; selected: boolean }) {
  const showPane = useMissionStore((s) => s.showPane);
  return (
    <button
      type="button"
      className={`agent-card${selected ? ' selected' : ''}`}
      aria-pressed={selected}
      data-testid="agent-node"
      data-agent-key={node.key}
      data-state={node.state}
      data-unresolved={node.unresolved || undefined}
      data-origin={node.origin}
      onClick={() => showPane(laneId, { kind: 'agent', key: node.key })}
      title={node.title}
    >
      <span className="agent-card-top">
        <Bot size={16} />
        <span className="agent-card-status" data-testid="agent-node-state">
          <span className="agent-state-dot" data-state={node.unresolved ? 'unknown' : node.state} />
          {node.unresolved ? 'Outcome unknown' : LABEL[node.state]}
        </span>
        {selected ? (
          <Check size={14} className="agent-card-check" />
        ) : (
          <ArrowRight size={14} className="agent-card-open" />
        )}
      </span>
      <span className="agent-card-name">{node.title}</span>
      <span className="agent-card-meta">
        {node.reported.toolUses !== undefined ? `${node.reported.toolUses} tool calls` : 'View activity'}
        {node.childKeys.length > 0 && ` · ${node.childKeys.length} subagents`}
        {node.origin !== 'execution' && ` · ${node.origin}`}
      </span>
    </button>
  );
}

/** A single bounded row, with explicit paging controls when more cards are offscreen. */
function AgentCards({
  label,
  selectedKey,
  children,
}: {
  label: string;
  selectedKey?: string;
  children: ReactNode;
}) {
  const id = useId();
  const rail = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const measure = useCallback(() => {
    const el = rail.current;
    if (!el) return;
    const next = { start: el.scrollLeft < 2, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2 };
    setEdges((old) => (old.start === next.start && old.end === next.end ? old : next));
  }, []);
  useEffect(() => {
    const el = rail.current;
    if (!el) return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    measure();
    return () => observer.disconnect();
  }, [measure]);
  useEffect(() => {
    // Keep selection visible without moving the page or the conversation's reading position.
    if (!selectedKey) return;
    const el = rail.current;
    if (!el) return;
    const reveal = () => {
      const card = el.querySelector<HTMLElement>('[aria-pressed="true"]');
      if (card) {
        const left = card.getBoundingClientRect().left - el.getBoundingClientRect().left + el.scrollLeft;
        if (left < el.scrollLeft) el.scrollLeft = left;
        else if (left + card.offsetWidth > el.scrollLeft + el.clientWidth)
          el.scrollLeft = left + card.offsetWidth - el.clientWidth;
      }
      measure();
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(el);
    return () => observer.disconnect();
  }, [selectedKey, measure]);
  return (
    <>
      <div className="agent-cards-heading">
        <span className="truncate" title={label}>
          {label}
        </span>
        <span className="agent-cards-hint">Select to inspect</span>
        <div className="agent-cards-paging">
          <button
            type="button"
            aria-label={`Previous ${label.toLowerCase()}`}
            aria-controls={id}
            disabled={edges.start}
            onClick={() =>
              rail.current?.scrollBy({ left: -rail.current.clientWidth * 0.8, behavior: 'auto' })
            }
          >
            <ArrowLeft size={14} />
          </button>
          <button
            type="button"
            aria-label={`Next ${label.toLowerCase()}`}
            aria-controls={id}
            disabled={edges.end}
            onClick={() => rail.current?.scrollBy({ left: rail.current.clientWidth * 0.8, behavior: 'auto' })}
          >
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
      <div id={id} className="agent-cards-scroll" ref={rail} onScroll={measure}>
        <div className="agent-cards-row">{children}</div>
      </div>
    </>
  );
}
