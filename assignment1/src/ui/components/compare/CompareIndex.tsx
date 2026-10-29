'use client';
import Link from 'next/link';
import { AppChrome } from '@/ui/components/chrome/AppChrome';
import { formatClock } from '@/ui/format';
import { trpc } from '@/ui/trpc/client';

export function CompareIndex() {
  const groups = trpc.fanout.list.useQuery(undefined, { refetchInterval: 5000 });
  return (
    <AppChrome>
      <div className="page" data-testid="compare-index">
        <h1>Fan-out races</h1>
        {/* This page had neither of these: a slow or failing list simply showed an empty heading,
            which reads exactly like "you have never raced anything". */}
        {groups.error && (
          <p className="error" role="alert" data-testid="compare-index-error">
            Could not load the races — {groups.error.message}{' '}
            <button type="button" className="btn" onClick={() => groups.refetch()}>
              Try again
            </button>
          </p>
        )}
        {!groups.data && !groups.error && groups.isPending && (
          <p className="muted" data-testid="compare-index-loading">
            Loading races…
          </p>
        )}
        {groups.data?.length === 0 && (
          <p className="muted">No races yet. Use Fan out on the Board to send one prompt to several lanes.</p>
        )}
        <ul className="group-list">
          {groups.data?.map((g) => (
            <li key={g.id} data-testid="group-row">
              <Link href={`/compare/${g.id}`} className="group-row">
                <span className="group-prompt truncate">{g.prompt}</span>
                <span className="faint mono">
                  {g.repoRoot.split('/').pop()} @ {g.baseCommit.slice(0, 7)}
                </span>
                <span className="faint">{formatClock(g.createdAt)}</span>
                {g.keptRunId && <span className="pill pill-finished">kept</span>}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </AppChrome>
  );
}
