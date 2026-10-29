'use client';
import { FolderOpen, GitBranch, MessagesSquare, PackageOpen, PanelLeft, X } from 'lucide-react';
import { type ReactNode, useState } from 'react';
import type { LaneDto } from '@/core/types';
import { Composer } from '@/ui/components/lane/Composer';
import { LaneHeader } from '@/ui/components/lane/LaneHeader';
import { LaneRuns } from '@/ui/components/lane/LaneRuns';
import { LaneOutline } from '@/ui/components/outline/LaneOutline';
import { useLaneHydration } from '@/ui/hooks/useLaneHydration';
import { useSession } from '@/ui/hooks/useSession';
import { CONVERSATION, useMissionStore } from '@/ui/store/missionStore';
import { laneHueIndex, laneHueStyle } from '@/ui/theme/laneHue';
import { trpc } from '@/ui/trpc/client';
import { AgentMap } from './AgentMap';
import { AgentPanel } from './AgentPanel';
import { BrowserEvidence, BrowserWarning } from './BrowserStatus';
import { NewSessionDialog } from './NewSessionDialog';
import { OutputsPanel } from './OutputsPanel';
import { SessionRail } from './SessionRail';
import { WrapUpInputs } from './WrapUpInputs';

/**
 * Where this session is being read, which decides what it carries with it.
 *
 * - `focus` brings the project rail.
 * - `column` belongs to a side-by-side workspace, which owns the shared rail.
 * Navigation becomes a drawer when the session's available width is narrow.
 */
export type SessionLayout = 'focus' | 'column';

/** A session with one navigation pane and one selected trajectory. */
export function SessionView({
  laneId,
  lane,
  layout = 'focus',
}: {
  laneId: string;
  lane: LaneDto;
  layout?: SessionLayout;
}) {
  const position = useMissionStore((s) => s.laneOrder.indexOf(laneId));
  const showPane = useMissionStore((s) => s.showPane);
  const [newSession, setNewSession] = useState(false);
  const { graph, pane, node, nodeRun, browser } = useSession(laneId);
  // What this session's directory *is*, for a header that would otherwise show a path deep inside
  // the console's own data directory and tell the operator nothing.
  const workspace = trpc.workspaces.get.useQuery({ laneId });
  // What this session was *given*, when it was given anything: a wrap-up carries a captured package
  // of other sessions' work, and that is a third thing to look at beside the conversation and the
  // files. Ordinary sessions answer `null` and show no such tab.
  const wrapUp = trpc.wrapups.get.useQuery({ laneId });
  const pkg = wrapUp.data ?? null;
  useLaneHydration(laneId);
  // A stored pane naming inputs for a session with no package would leave the body with nothing to
  // render; the conversation is what it falls back to, the same way an unknown delegate key does.
  const showingInputs = pane.kind === 'inputs' && pkg !== null;
  const hasAgents = graph.nodes.length > 0;
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigation = (steps: ReactNode) => (
    <aside className="session-navigation" data-open={navigationOpen} aria-label="Tool calls">
      <div className="session-navigation-heading">
        <span>Tool calls</span>
        <button
          type="button"
          className="navigation-close"
          aria-label="Close navigation"
          onClick={() => setNavigationOpen(false)}
        >
          <X size={14} />
        </button>
      </div>
      {steps}
    </aside>
  );
  return (
    <>
      {layout === 'focus' && (
        <SessionRail
          projectRoot={lane.projectRoot}
          selectedId={laneId}
          onNewSession={() => setNewSession(true)}
        />
      )}
      <section
        className="lane session"
        style={laneHueStyle(laneHueIndex(lane, Math.max(0, position)))}
        data-lane-id={laneId}
        data-testid="lane"
        data-outline="sidebar"
        data-layout={layout}
        data-pane={pane.kind}
      >
        <LaneHeader lane={lane} openHref={layout === 'column' ? `/lanes/${laneId}` : undefined} />
        {workspace.data && (
          <div className="session-base faint" data-testid="session-base">
            <GitBranch size={12} />
            <span className="mono truncate" title={workspace.data.branch}>
              {workspace.data.branch}
            </span>
            <span>
              from {workspace.data.baseCommit.slice(0, 10)}
              {workspace.data.repoRoot ? ` of ${workspace.data.repoRoot.split('/').pop()}` : ''}
            </span>
            <span className="faint">
              · a checkout of its own; uncommitted work in the folder it came from was not copied
            </span>
          </div>
        )}
        {pkg && (
          <div className="session-base faint" data-testid="session-wrapup-base">
            <PackageOpen size={12} />
            <span>
              Brought together from {pkg.sources.length} session{pkg.sources.length === 1 ? '' : 's'}
            </span>
            {pkg.partial && (
              <span className="caution" data-testid="session-wrapup-partial">
                including work that did not finish
              </span>
            )}
            <span className="faint">· the captured package is in this folder, under inputs/</span>
          </div>
        )}
        <BrowserWarning browser={browser} />
        <div className="session-tabs" role="tablist" aria-label="Session views">
          {pane.kind !== 'outputs' && !showingInputs && (
            <button
              type="button"
              className="session-tab navigation-toggle"
              aria-label="Open tool calls"
              aria-expanded={navigationOpen}
              onClick={() => setNavigationOpen((v) => !v)}
            >
              <PanelLeft size={15} /> Tool calls
            </button>
          )}
          <button
            type="button"
            role="tab"
            aria-selected={pane.kind !== 'outputs' && !showingInputs}
            className={`session-tab${pane.kind === 'outputs' || showingInputs ? '' : ' active'}`}
            onClick={() => showPane(laneId, CONVERSATION)}
            data-testid="tab-conversation"
          >
            <MessagesSquare size={13} /> Conversation
            {pane.kind === 'agent' && node && <span className="faint truncate"> · {node.title}</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={pane.kind === 'outputs'}
            className={`session-tab${pane.kind === 'outputs' ? ' active' : ''}`}
            onClick={() => showPane(laneId, { kind: 'outputs' })}
            data-testid="tab-outputs"
          >
            {/* "Files", not "Outputs": this lists the session's working folder, which includes
              everything that was there before the agent arrived. */}
            <FolderOpen size={13} /> Files
          </button>
          {pkg && (
            <button
              type="button"
              role="tab"
              aria-selected={showingInputs}
              className={`session-tab${showingInputs ? ' active' : ''}`}
              onClick={() => showPane(laneId, { kind: 'inputs' })}
              data-testid="tab-inputs"
            >
              <PackageOpen size={13} /> Inputs
            </button>
          )}
          <span className="spacer" />
          <BrowserEvidence browser={browser} />
        </div>
        {hasAgents && pane.kind !== 'outputs' && !showingInputs && (
          <AgentMap laneId={laneId} graph={graph} pane={pane} />
        )}
        <div className="lane-body">
          {showingInputs ? (
            <WrapUpInputs laneId={laneId} wrapUp={pkg} />
          ) : pane.kind === 'outputs' ? (
            <OutputsPanel laneId={laneId} />
          ) : node && nodeRun ? (
            <AgentPanel
              laneId={laneId}
              node={node}
              run={nodeRun}
              graph={graph}
              navigation={navigation}
              onNavigate={() => setNavigationOpen(false)}
            />
          ) : (
            <>
              {navigation(
                <LaneOutline laneId={laneId} variant="sidebar" onNavigate={() => setNavigationOpen(false)} />,
              )}
              <LaneRuns laneId={laneId} />
            </>
          )}
        </div>
        {/* The composer always talks to the session itself, whichever pane is open: a native delegate
          has no separate conversation to continue, and offering one would be a control the backend
          cannot honour. */}
        <Composer
          laneId={laneId}
          archived={lane.archivedAt !== null}
          context={
            showingInputs
              ? 'inputs'
              : pane.kind === 'conversation' || pane.kind === 'inputs'
                ? undefined
                : pane.kind
          }
        />
        {newSession && <NewSessionDialog from={lane} onClose={() => setNewSession(false)} />}
      </section>
    </>
  );
}
