'use client';
import { Archive, ExternalLink, GitBranch } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';
import { shortenPath } from '@/core/summarize';
import type { LaneDto } from '@/core/types';
import { useArchiveSession } from '@/ui/components/session/SessionRail';
import { formatTokens, formatUsd } from '@/ui/format';
import { projectHref } from '@/ui/project';
import { useMissionStore } from '@/ui/store/missionStore';
import { latestRun, useLaneStatus } from '@/ui/store/selectors';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';

/**
 * What this session is, what it is doing, and the one lifecycle action that is not Stop.
 *
 * Two controls were removed rather than relabelled. The permission pill looked like a status badge
 * and silently switched the agent between the standard allowlist and no permission checks at all;
 * permissions are policy now, and this shows nothing to click. The browser pill was the same shape
 * of mistake in the other direction — the browser is always on, so a switch for it would be a
 * control that decides nothing. What the browser actually *did* is read-only and lives beside the
 * tabs (`BrowserEvidence`).
 *
 * Archive is not Stop, and cannot become it: the server refuses to archive a session it is still
 * driving, and the refusal is shown rather than resolved by cancelling the work.
 */
export function LaneHeader({
  lane,
  openHref,
}: {
  lane: LaneDto;
  /**
   * Where this session lives on its own, when it is currently sharing the screen.
   *
   * A workspace column is the whole session, so the way out of it is not "expand" or "maximise" but
   * an ordinary link to the page it already has — which is also what makes it shareable, bookmarkable
   * and reachable with the keyboard like every other link in the console.
   */
  openHref?: string | undefined;
}) {
  const laneId = lane.id;
  const router = useRouter();
  const status = useLaneStatus(laneId);
  const last = useMissionStore((s) => latestRun(s.runs, laneId));
  const home = trpc.system.info.useQuery().data?.home;
  const archiveSession = useArchiveSession();
  const update = trpc.lanes.update.useMutation({
    onSuccess: (l) => l && useMissionStore.getState().upsertLane(l),
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not rename the session', text: e.message }),
  });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(lane.name);
  const cancelledRef = useRef(false);
  const running = status === 'running';
  const activity = running
    ? (last?.activity ?? (last?.thinking ? 'Thinking…' : 'Working…'))
    : last?.summary?.detail;
  return (
    <header className="lane-header">
      <div className="lane-header-row">
        {editing ? (
          <input
            className="input lane-name-input"
            value={draft}
            // biome-ignore lint/a11y/noAutofocus: rename input replaces a just-clicked element in place; the user's focus is already there, so autofocus keeps typing seamless.
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            // Typing replaces the old name instead of appending to it.
            onFocus={(e) => e.currentTarget.select()}
            onBlur={() => {
              if (cancelledRef.current) {
                cancelledRef.current = false;
                setEditing(false);
                return;
              }
              setEditing(false);
              if (draft.trim() && draft !== lane.name) update.mutate({ laneId, name: draft.trim() });
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
              if (e.key === 'Escape') {
                cancelledRef.current = true;
                setDraft(lane.name);
                setEditing(false);
              }
            }}
            data-testid="lane-name-input"
          />
        ) : (
          // biome-ignore lint/a11y/useSemanticElements: must stay a span inline with the lane-header text; role="button" plus tabIndex/onKeyDown makes it keyboard/AT operable.
          <span
            className="lane-name"
            tabIndex={0}
            role="button"
            aria-label={`${lane.name} — press Enter to rename`}
            onDoubleClick={() => {
              setDraft(lane.name);
              setEditing(true);
            }}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' && e.key !== 'F2' && e.key !== ' ') return;
              e.preventDefault();
              setDraft(lane.name);
              setEditing(true);
            }}
            title="Double-click to rename"
            data-testid="lane-name"
          >
            {lane.name}
          </span>
        )}
        {lane.archivedAt !== null && (
          <span className="pill" data-testid="lane-archived">
            archived
          </span>
        )}
        <span
          className={`pill pill-${status === 'idle' ? 'cancelled' : status}`}
          data-testid="lane-status"
          data-status={status}
        >
          {status}
        </span>
        <span className="spacer" />
        {last && last.context.tokens > 0 && (
          <span
            className="faint mono"
            title="Tokens the model is carrying right now"
            data-testid="lane-context"
          >
            {formatTokens(last.context.tokens)} in context
          </span>
        )}
        {/* The compact figure only, and only once the run is over: the footer carries the full
            line (cost · duration · turns), and repeating it here would be noise. */}
        {last?.numbers && (last.status === 'finished' || last.status === 'failed') && (
          <span
            className="faint mono"
            title="Cost reported by the CLI's result event"
            data-testid="lane-cost"
          >
            {formatUsd(last.numbers.costUsd)}
          </span>
        )}
        {openHref && (
          <Link
            href={openHref}
            className="btn btn-ghost"
            aria-label={`Open ${lane.name} on its own`}
            title="Open this session on its own"
            data-testid="lane-open"
          >
            <ExternalLink size={14} />
          </Link>
        )}
        {lane.archivedAt === null && (
          <button
            type="button"
            className="btn btn-ghost"
            aria-label="Archive this session"
            // Says what it keeps, because the control it replaces did not.
            title="Archive — keeps every run and file, and can be reopened"
            onClick={async () => {
              if (await archiveSession(laneId)) router.push(projectHref(lane.projectRoot));
            }}
            data-testid="lane-archive"
          >
            <Archive size={14} />
          </button>
        )}
      </div>
      <div className="lane-header-row lane-header-sub">
        <span className="mono truncate faint" title={lane.cwd} data-testid="lane-path">
          {shortenPath(lane.cwd, undefined, home)}
        </span>
        {lane.isolated && (
          <span className="pill" data-testid="lane-isolated">
            <GitBranch size={11} /> own checkout
          </span>
        )}
        {activity && (
          <span className="truncate muted lane-activity" data-testid="lane-activity">
            {activity}
          </span>
        )}
      </div>
    </header>
  );
}
