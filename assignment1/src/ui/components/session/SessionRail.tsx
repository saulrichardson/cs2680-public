'use client';
import { Archive, ArchiveRestore, Columns3, ExternalLink, FolderGit2, Plus } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import type { LaneDto } from '@/core/types';
import { formatAgo } from '@/ui/format';
import { projectHref } from '@/ui/project';
import { useMissionStore } from '@/ui/store/missionStore';
import { useLaneStatus, useOrderedSessions } from '@/ui/store/selectors';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';

/**
 * Putting sessions into the project workspace's columns rather than opening one.
 *
 * The rail is the project's one piece of navigation, so a row does what the surrounding view is
 * for. In the workspace a click adds or removes a column; opening a session on its own stays, as
 * its own small control.
 */
export interface RailPick {
  /** The sessions in the columns, in the order they appear. */
  ids: readonly string[];
  max: number;
  toggle(laneId: string): void;
}

/**
 * The conversations in this project, and the way to start another.
 *
 * Only this project's. Seeing every conversation on the machine listed beside one piece of work
 * was the complaint that produced this, and the filter is the recorded project rather than a path
 * prefix, so a package inside a monorepo keeps its own list.
 *
 * Archived sessions are behind a disclosure rather than gone: archiving is reversible and keeps
 * every run and file, so hiding them permanently would be the same mistake as deleting them.
 */
export function SessionRail({
  projectRoot,
  selectedId,
  onNewSession,
  pick,
}: {
  projectRoot: string;
  selectedId?: string | undefined;
  onNewSession(): void;
  /** Present in the project workspace: rows choose columns instead of navigating. */
  pick?: RailPick | undefined;
}) {
  const [showArchived, setShowArchived] = useState(false);
  const all = useMissionStore((s) => s.lanes);
  const active = useOrderedSessions(
    Object.values(all).filter((l) => l.projectRoot === projectRoot && l.archivedAt === null),
  );
  const archived = trpc.lanes.list.useQuery(
    { projectRoot, includeArchived: true },
    { enabled: showArchived },
  );
  const archivedOnly = (archived.data ?? []).filter((l) => l.archivedAt !== null);

  return (
    <aside className="rail" data-testid="session-rail">
      <header className="rail-head">
        <h2>Sessions</h2>
        <button
          type="button"
          className="btn btn-primary btn-small"
          onClick={onNewSession}
          data-testid="new-session"
        >
          <Plus size={13} /> New
        </button>
      </header>
      {/* The way to the project's own workspace, from inside a session. The breadcrumb reaches it
          too, but the list of this project's sessions is where somebody looking for another one is
          already looking. */}
      {pick ? (
        <p className="faint rail-note" data-testid="rail-pick-note">
          {pick.ids.length} of {pick.max} shown. Choose a session to put it beside the others.
        </p>
      ) : (
        <Link href={projectHref(projectRoot)} className="rail-note-link" data-testid="rail-workspace">
          <Columns3 size={12} /> Side by side
        </Link>
      )}
      <ul className="rail-list">
        {active.map((lane) => (
          <li key={lane.id}>
            {pick ? (
              <PickRow lane={lane} pick={pick} />
            ) : (
              <RailRow lane={lane} selected={lane.id === selectedId} />
            )}
          </li>
        ))}
        {active.length === 0 && (
          <li className="faint rail-empty" data-testid="rail-empty">
            No active sessions in this project.
          </li>
        )}
      </ul>
      <button
        type="button"
        className="rail-archived-toggle"
        aria-expanded={showArchived}
        onClick={() => setShowArchived((v) => !v)}
        data-testid="rail-archived-toggle"
      >
        {showArchived ? 'Hide archived' : 'Archived'}
      </button>
      {showArchived && (
        <ul className="rail-list" data-testid="rail-archived">
          {archivedOnly.map((lane) => (
            <li key={lane.id}>
              <ArchivedRow lane={lane} />
            </li>
          ))}
          {archivedOnly.length === 0 && <li className="faint rail-empty">Nothing archived.</li>}
        </ul>
      )}
    </aside>
  );
}

function RailRow({ lane, selected }: { lane: LaneDto; selected: boolean }) {
  const status = useLaneStatus(lane.id);
  return (
    <Link
      href={`/lanes/${lane.id}`}
      className={`rail-row${selected ? ' selected' : ''}`}
      aria-current={selected ? 'page' : undefined}
      data-testid="rail-session"
      data-lane={lane.id}
      data-status={status}
    >
      <span className={`dot dot-${status}`} aria-hidden="true" />
      <span className="truncate">{lane.name}</span>
      {lane.isolated && <FolderGit2 size={11} className="faint" aria-label="own checkout" />}
      <span className="faint rail-when">{formatAgo(lane.lastActivityAt)}</span>
    </Link>
  );
}

/**
 * A rail row in the workspace: a toggle for the columns, with its own way out to the session.
 *
 * The number is the column it occupies, which is how the rail and that column say they are the same
 * thing. `aria-pressed`, not a checkbox: one control that turns a column on and off.
 */
function PickRow({ lane, pick }: { lane: LaneDto; pick: RailPick }) {
  const status = useLaneStatus(lane.id);
  const position = pick.ids.indexOf(lane.id);
  const shown = position >= 0;
  return (
    <div className="rail-pick" data-testid="rail-pick" data-lane={lane.id} data-shown={shown || undefined}>
      <button
        type="button"
        className={`rail-row rail-pick-main${shown ? ' selected' : ''}`}
        aria-pressed={shown}
        onClick={() => pick.toggle(lane.id)}
        data-testid="rail-pick-toggle"
        data-status={status}
      >
        <span className={`dot dot-${status}`} aria-hidden="true" />
        <span className="truncate">{lane.name}</span>
        {lane.isolated && <FolderGit2 size={11} className="faint" aria-label="own checkout" />}
        {shown ? (
          <span className="rail-pick-slot">
            <span className="sr-only">column </span>
            {position + 1}
          </span>
        ) : (
          <span className="faint rail-when">{formatAgo(lane.lastActivityAt)}</span>
        )}
      </button>
      <Link
        href={`/lanes/${lane.id}`}
        className="rail-pick-open"
        aria-label={`Open ${lane.name} on its own`}
        title="Open on its own"
        data-testid="rail-pick-open"
      >
        <ExternalLink size={12} />
      </Link>
    </div>
  );
}

function ArchivedRow({ lane }: { lane: LaneDto }) {
  const utils = trpc.useUtils();
  const reopen = trpc.lanes.reopen.useMutation({
    onSuccess: (restored) => {
      useMissionStore.getState().upsertLane(restored);
      void utils.lanes.list.invalidate();
    },
    onError: (error) =>
      useToasts.getState().push({
        kind: 'error',
        title: 'Could not reopen the session',
        text: error.message,
      }),
  });
  return (
    <div className="rail-row" data-testid="rail-archived-session" data-lane={lane.id}>
      <Archive size={11} className="faint" aria-hidden="true" />
      <Link href={`/lanes/${lane.id}`} className="truncate">
        {lane.name}
      </Link>
      <button
        type="button"
        className="btn btn-ghost btn-small"
        data-testid="rail-reopen"
        disabled={reopen.isPending}
        onClick={() => reopen.mutate({ laneId: lane.id })}
      >
        <ArchiveRestore size={12} /> Reopen
      </button>
    </div>
  );
}

/** Archiving from the session header, with the server's refusal shown rather than worked around. */
export function useArchiveSession(): (laneId: string) => Promise<boolean> {
  const utils = trpc.useUtils();
  const archive = trpc.lanes.archive.useMutation();
  return async (laneId: string) => {
    try {
      await archive.mutateAsync({ laneId });
      useMissionStore.getState().removeLane(laneId);
      utils.lanes.list.invalidate();
      return true;
    } catch (err) {
      useToasts.getState().push({
        kind: 'error',
        title: 'This session was not archived',
        text: err instanceof Error ? err.message : String(err),
      });
      return false;
    }
  };
}
