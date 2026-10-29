'use client';
import { type ReactNode, useEffect, useState } from 'react';
import { TopBar } from '@/ui/components/chrome/TopBar';
import { CommandPalette, ImportDialog } from '@/ui/components/palette/CommandPalette';
import { useUnknownRuns } from '@/ui/hooks/useUnknownRuns';
import { useMissionStore } from '@/ui/store/missionStore';
import { usePalette } from '@/ui/store/palette';
import { trpc } from '@/ui/trpc/client';

/**
 * Everything every page of the console has: the trail at the top, the command palette, the session
 * list they work from, and the fetch that fills in a run first seen over the socket.
 *
 * The session list is hydrated here, once, as a *global* answer — which is what lets a session
 * archived in another tab be dropped from this one rather than lingering as a ghost. Views that
 * show one project filter that list; they do not fetch a narrower one and hydrate it, because a
 * scoped answer is not a snapshot of everything (see `hydrateLanes`).
 */
export function AppChrome({
  project,
  session,
  children,
}: {
  project?: string | undefined;
  session?: string | undefined;
  children: ReactNode;
}) {
  const [importing, setImporting] = useState(false);
  const pending = usePalette((s) => s.pending);
  const lanes = trpc.lanes.list.useQuery();
  useUnknownRuns();

  useEffect(() => {
    if (lanes.data) useMissionStore.getState().hydrateLanes(lanes.data, { kind: 'all' });
  }, [lanes.data]);

  // The palette closes itself when it asks for a dialog, so the request is consumed here.
  useEffect(() => {
    if (!pending) return;
    setImporting(true);
    usePalette.getState().request(null);
  }, [pending]);

  return (
    <>
      <TopBar project={project} session={session} />
      <main className="app-main">{children}</main>
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
      <CommandPalette />
    </>
  );
}
