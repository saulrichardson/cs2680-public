'use client';
import { useEffect, useRef, useState } from 'react';
import { AppChrome } from '@/ui/components/chrome/AppChrome';
import { useLaneHydration } from '@/ui/hooks/useLaneHydration';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';
import { CompareStrip } from './CompareStrip';
import { DiffTab } from './DiffTab';
import { compareIsStale, comparePollMs, raceIsRunning } from './staleness';
import { TimelineTab } from './TimelineTab';
import { TrajectoriesTab } from './TrajectoriesTab';

type Tab = 'timeline' | 'diffs' | 'trajectories';
const TABS: Tab[] = ['timeline', 'diffs', 'trajectories'];

/** Hydrates one lane's runs + events into the store so DiffTab/TimelineTab (which read views
 * straight from the store) have something to render, regardless of which tab is active. */
function LaneHydrator({ laneId }: { laneId: string }) {
  useLaneHydration(laneId);
  return null;
}

/** How often an open Compare asks again while it has a reason to (see `comparePollMs`). */
const POLL_MS = 2000;

export function Compare({ groupId }: { groupId: string }) {
  const [tab, setTab] = useState<Tab>('timeline');
  // Why this page is still asking, if it is. Held in a ref because React Query reads
  // `refetchInterval` after each fetch, and the answer depends on the live store as well as on the
  // payload the query itself is holding.
  const poll = useRef<number | false>(false);
  const compare = trpc.fanout.compare.useQuery({ groupId }, { refetchInterval: () => poll.current });
  const data = compare.data;
  const lanes = data?.lanes;
  // The snapshot used to decide both things about itself: when to keep polling, and whether it was
  // still current — by looking only at the runs it already contained. A follow-up execution started
  // from another tab is in none of them, so an open Compare never came back for it (readiness
  // review R3). Both questions are now asked of this race's *lanes* in the live store, and being
  // behind is itself a reason to keep asking — an answer computed before a follow-up finished is
  // stale on arrival, and an edge-triggered refetch would never fire again.
  const laneRunning = useMissionStore((s) => (lanes ? raceIsRunning(lanes, s) : false));
  const stale = useMissionStore((s) => (lanes ? compareIsStale(lanes, s) : false));
  poll.current = useMissionStore((s) => comparePollMs(lanes, s, POLL_MS));
  const refetch = compare.refetch;
  // The poll is the owner; this only avoids waiting for its next tick when the news has just
  // arrived over the socket.
  useEffect(() => {
    if (stale) void refetch();
  }, [stale, refetch]);
  // And when the last execution ends, come back at once rather than up to one poll later — the
  // strip would otherwise show a live "finished" next to numbers from a 2-second-old payload.
  const wasRunning = useRef(laneRunning);
  useEffect(() => {
    if (wasRunning.current && !laneRunning) void refetch();
    wasRunning.current = laneRunning;
  }, [laneRunning, refetch]);
  return (
    <AppChrome>
      <div className="page compare" data-testid="compare">
        {/* In place of the content when there is nothing to show; above it when a refetch failed
              over a page that is still good. */}
        {compare.error && (
          <p className="error" role="alert" data-testid="compare-error">
            Could not load this comparison — {compare.error.message}
          </p>
        )}
        {/* `fanout.compare` runs a git diff per lane, so the first load is not instant. Say the
              page is working rather than show an empty frame under the top bar. */}
        {!data && !compare.error && compare.isPending && (
          <p className="muted" data-testid="compare-loading">
            Loading comparison…
          </p>
        )}
        {data && (
          <>
            {data.lanes.map((lane) => (
              <LaneHydrator key={lane.laneId} laneId={lane.laneId} />
            ))}
            <header className="compare-head">
              <h1 className="truncate" title={data.prompt}>
                {data.prompt}
              </h1>
              <span className="faint mono">
                {data.repoRoot} @ {data.baseCommit.slice(0, 7)}
              </span>
            </header>
            <CompareStrip data={data} onChanged={() => compare.refetch()} />
            {/* a `<nav>` landmark can't also carry the `tablist` widget role, so this is a plain div */}
            <div className="segmented tabs" aria-label="Compare tabs" role="tablist">
              {TABS.map((t, i) => (
                <button
                  type="button"
                  key={t}
                  className={`seg${tab === t ? ' active' : ''}`}
                  onClick={() => setTab(t)}
                  // One tab stop for the set, arrows to move inside it — the tablist pattern.
                  tabIndex={tab === t ? 0 : -1}
                  onKeyDown={(e) => {
                    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
                    e.preventDefault();
                    const step = e.key === 'ArrowRight' ? 1 : TABS.length - 1;
                    const next = TABS[(i + step) % TABS.length];
                    if (!next) return;
                    setTab(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }}
                  data-testid={`tab-${t}`}
                  id={`tab-${t}`}
                  role="tab"
                  aria-selected={tab === t}
                  aria-controls={`panel-${t}`}
                >
                  {t[0]?.toUpperCase()}
                  {t.slice(1)}
                </button>
              ))}
            </div>
            <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              {tab === 'timeline' && <TimelineTab data={data} />}
              {tab === 'diffs' && <DiffTab data={data} />}
              {tab === 'trajectories' && <TrajectoriesTab data={data} />}
            </div>
          </>
        )}
      </div>
    </AppChrome>
  );
}
