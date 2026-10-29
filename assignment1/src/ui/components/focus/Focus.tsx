'use client';
import { ArchiveRestore } from 'lucide-react';
import Link from 'next/link';
import { useEffect } from 'react';
import type { LaneDto } from '@/core/types';
import { AppChrome } from '@/ui/components/chrome/AppChrome';
import { SessionView } from '@/ui/components/session/SessionView';
import { useMissionStore } from '@/ui/store/missionStore';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';
import { Inspector } from './Inspector';

/**
 * One session, with the inspector beside it when a call is selected.
 *
 * Two lookups, deliberately. The store holds the *active* sessions of every project, which is
 * what the rail and the running indicator work from; an archived session is not in it, and a
 * direct link to one has to keep working — its conversation and files are exactly what somebody
 * comes back for. So the page also asks for this one session by id, and renders it read-only with
 * a Reopen when it turns out to be archived.
 */
export function Focus({ laneId }: { laneId: string }) {
  const known = useMissionStore((s) => s.lanes[laneId]);
  const fromServer = trpc.lanes.get.useQuery({ laneId }, { retry: false });
  const lane = known ?? fromServer.data ?? undefined;
  const selection = useMissionStore((s) => s.selection);
  useEffect(() => () => useMissionStore.getState().select(null, null), []);
  // Remembered for the project, so returning to it lands here again. Explicit navigation only.
  useEffect(() => {
    if (lane) useMissionStore.getState().selectSession(lane.projectRoot, lane.id);
  }, [lane]);
  const selected = selection.laneId === laneId ? selection.call : null;

  if (!lane)
    return (
      <AppChrome>
        {fromServer.isPending ? null : (
          <div className="empty-board" data-testid="lane-missing">
            <h1>This session does not exist.</h1>
            <p className="muted">The link may be out of date.</p>
            <Link href="/" className="btn btn-primary">
              Start a session
            </Link>
          </div>
        )}
      </AppChrome>
    );

  return (
    <AppChrome project={lane.projectRoot} session={lane.name}>
      <div className="focus" data-testid="focus" data-inspector={selected ? 'open' : 'closed'}>
        {lane.archivedAt !== null && <ArchivedBanner lane={lane} />}
        <SessionView laneId={laneId} lane={lane} />
        {selected && <Inspector laneId={laneId} call={selected} />}
      </div>
    </AppChrome>
  );
}

/**
 * An archived session reads normally and accepts nothing. The server refuses new work either way;
 * this is what says so before the operator types, and the one control that changes it.
 */
function ArchivedBanner({ lane }: { lane: LaneDto }) {
  const utils = trpc.useUtils();
  const reopen = trpc.lanes.reopen.useMutation({
    onSuccess: (restored) => {
      useMissionStore.getState().upsertLane(restored);
      utils.lanes.list.invalidate();
    },
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not reopen the session', text: e.message }),
  });
  return (
    <div className="archived-banner" data-testid="archived-banner">
      <span>Archived. Its conversation and files are here to read; continuing needs it reopened.</span>
      <button
        type="button"
        className="btn"
        onClick={() => reopen.mutate({ laneId: lane.id })}
        data-testid="archived-reopen"
      >
        <ArchiveRestore size={13} /> Reopen
      </button>
    </div>
  );
}
