'use client';
import { Columns3, PackageOpen } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { LaneDto } from '@/core/types';
import { WRAPUP_MAX_SOURCES } from '@/core/wrapup';
import { AppChrome } from '@/ui/components/chrome/AppChrome';
import { BringTogetherDialog } from '@/ui/components/session/BringTogetherDialog';
import { NewSessionDialog } from '@/ui/components/session/NewSessionDialog';
import { SessionForm } from '@/ui/components/session/SessionForm';
import { SessionRail } from '@/ui/components/session/SessionRail';
import { SessionView } from '@/ui/components/session/SessionView';
import { NO_WORKSPACE_SESSIONS, useMissionStore } from '@/ui/store/missionStore';
import { useOrderedSessions } from '@/ui/store/selectors';
import { trpc } from '@/ui/trpc/client';

/**
 * A project: its sessions, and up to three of them open side by side.
 *
 * This route used to redirect to one session, which made a project a signpost rather than a place.
 * A column is not a preview — it is `SessionView`, the same component the session page renders,
 * with its conversation, agent cards, tool outline, Files and composer. The project rail is shared;
 * narrower columns put the tool outline in a drawer. Each header also opens the session on its own.
 *
 * With nothing chosen it opens the conversation the operator was last reading, else the most
 * current one.
 */
export function ProjectView({ projectRoot }: { projectRoot: string }) {
  const lanes = trpc.lanes.list.useQuery({ projectRoot });
  const known = useMissionStore((s) => s.lanes);
  const lastSelected = useMissionStore((s) => s.lastSessionByProject[projectRoot]);
  // A frozen constant rather than `?? []`: a selector that allocates on every snapshot read is the
  // unstable-selector mistake this project has a rule about.
  const chosen = useMissionStore((s) => s.workspaceByProject[projectRoot] ?? NO_WORKSPACE_SESSIONS);
  const [newSession, setNewSession] = useState(false);
  const [bringing, setBringing] = useState<string[] | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [selectionReady, setSelectionReady] = useState<string | null>(null);
  useEffect(() => {
    if (!lanes.data) return;
    if (!Object.hasOwn(useMissionStore.getState().workspaceByProject, projectRoot)) {
      try {
        const stored: unknown = JSON.parse(localStorage.getItem(`workspace:${projectRoot}`) ?? 'null');
        if (Array.isArray(stored) && stored.every((id) => typeof id === 'string'))
          useMissionStore
            .getState()
            .setWorkspaceSessions(
              projectRoot,
              [...new Set(stored)]
                .filter((id) => lanes.data.some((lane) => lane.id === id && lane.archivedAt === null))
                .slice(0, WRAPUP_MAX_SOURCES),
            );
      } catch {
        /* Reading preferences is optional when browser storage is unavailable. */
      }
    }
    setSelectionReady(projectRoot);
  }, [projectRoot, lanes.data]);
  useEffect(() => {
    if (selectionReady !== projectRoot || chosen === NO_WORKSPACE_SESSIONS) return;
    try {
      localStorage.setItem(`workspace:${projectRoot}`, JSON.stringify(chosen));
    } catch {
      /* Keep the in-memory selection when browser storage is unavailable. */
    }
  }, [chosen, projectRoot, selectionReady]);

  const active = useOrderedSessions(
    Object.values(known).filter((l) => l.projectRoot === projectRoot && l.archivedAt === null),
  );
  const byId = useMemo(() => new Map(active.map((l) => [l.id, l])), [active]);
  // Only sessions this project still has. A column whose session was archived in another tab is a
  // column nothing can fill, and the store drops it from here as soon as that is known.
  const columns = useMemo(
    () => chosen.map((id) => byId.get(id)).filter((l): l is LaneDto => l !== undefined),
    [chosen, byId],
  );
  // The one the operator left, if it is still part of this project's current work; otherwise the
  // most current. Only consulted when nothing has been chosen yet.
  const fallback = active.find((l) => l.id === lastSelected) ?? active[0];

  useEffect(() => {
    // Waits for the authoritative list: opening on whichever session happened to arrive first is
    // how the redirect this replaces used to send people to the wrong conversation.
    if (
      selectionReady !== projectRoot ||
      !lanes.isSuccess ||
      Object.hasOwn(useMissionStore.getState().workspaceByProject, projectRoot) ||
      !fallback
    )
      return;
    useMissionStore.getState().setWorkspaceSessions(projectRoot, [fallback.id]);
  }, [selectionReady, lanes.isSuccess, fallback, projectRoot]);

  // Which column a narrow screen is showing. Kept valid rather than trusted: the chosen column can
  // be closed from the rail while it is the one being read.
  const shown = columns.find((l) => l.id === focused) ?? columns[0];

  // With no session to branch from there is no dialog to open — the creation form *is* the page —
  // so New puts the cursor in it rather than doing nothing.
  const onNewSession = fallback ? () => setNewSession(true) : focusStartPrompt;
  const rail = (
    <SessionRail
      projectRoot={projectRoot}
      onNewSession={onNewSession}
      pick={{
        ids: chosen,
        max: WRAPUP_MAX_SOURCES,
        toggle: (laneId) =>
          useMissionStore.getState().toggleWorkspaceSession(projectRoot, laneId, WRAPUP_MAX_SOURCES),
      }}
    />
  );

  if (lanes.isSuccess && active.length === 0)
    return (
      <AppChrome project={projectRoot}>
        <div className="project" data-testid="project-view">
          {rail}
          <section className="start-card project-empty" data-testid="project-empty">
            <h1>Start a session here</h1>
            <p className="muted">Nothing active in this project yet.</p>
            {/* Pointed at the project's own folder: this page knows where it is. */}
            <SessionForm initialCwd={projectRoot} />
          </section>
        </div>
      </AppChrome>
    );

  return (
    <AppChrome project={projectRoot}>
      <div className="project workspace" data-testid="project-view" data-columns={columns.length}>
        {rail}
        <div className="workspace-main">
          <div className="workspace-bar" data-testid="workspace-bar">
            <span className="workspace-count faint">
              <Columns3 size={13} aria-hidden="true" />{' '}
              {columns.length === 1 ? '1 session' : `${columns.length} sessions`} of {WRAPUP_MAX_SOURCES}
            </span>
            {/* Only rendered by the stylesheet below the breakpoint where three columns stop being
                three readable columns; above it, switching between what is already on screen would
                be a control that does nothing. */}
            <nav className="workspace-switch" aria-label="Session shown">
              {columns.map((lane) => (
                <button
                  key={lane.id}
                  type="button"
                  className={`workspace-switch-tab${shown?.id === lane.id ? ' active' : ''}`}
                  aria-current={shown?.id === lane.id ? 'true' : undefined}
                  onClick={() => setFocused(lane.id)}
                  data-testid="workspace-switch-tab"
                >
                  <span className="truncate">{lane.name}</span>
                </button>
              ))}
            </nav>
            <span className="spacer" />
            <button
              type="button"
              className="btn"
              disabled={columns.length === 0}
              onClick={() => setBringing(columns.map((lane) => lane.id))}
              title="Start a new session from a captured copy of what these sessions produced"
              data-testid="bring-together"
            >
              <PackageOpen size={13} /> Bring together
            </button>
          </div>
          {lanes.error && (
            <p className="error" role="alert">
              {lanes.error.message}
            </p>
          )}
          <div className="workspace-columns" data-focused={shown?.id}>
            {columns.map((lane) => (
              <div
                className="workspace-column"
                key={lane.id}
                data-testid="workspace-column"
                data-lane={lane.id}
                data-shown={shown?.id === lane.id || undefined}
              >
                <SessionView laneId={lane.id} lane={lane} layout="column" />
              </div>
            ))}
            {columns.length === 0 && (
              <p className="faint workspace-empty" data-testid="workspace-empty">
                Nothing shown. Choose a session from the list to put it here.
              </p>
            )}
          </div>
        </div>
      </div>
      {newSession && <NewSessionDialog projectRoot={projectRoot} onClose={() => setNewSession(false)} />}
      {bringing && (
        <BringTogetherDialog projectRoot={projectRoot} laneIds={bringing} onClose={() => setBringing(null)} />
      )}
    </AppChrome>
  );
}

function focusStartPrompt(): void {
  document.querySelector<HTMLTextAreaElement>('[data-testid="start-prompt"]')?.focus();
}
