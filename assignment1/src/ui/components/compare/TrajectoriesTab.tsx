'use client';
import type { CompareResult } from '@/core/types';
import { BlockRow } from '@/ui/components/blocks/BlockRow';
import { RunFooter } from '@/ui/components/lane/RunFooter';
import { useLaneHydration } from '@/ui/hooks/useLaneHydration';
import { useLaneRuns } from '@/ui/store/missionStore';

function Column({ laneId, name }: { laneId: string; name: string }) {
  useLaneHydration(laneId);
  // The candidate's trajectory, which is the work this lane executed. A replay or an import in a
  // racing lane is a view of something done elsewhere and has no place in a comparison of what the
  // agents did — the lane itself still shows it.
  const runs = useLaneRuns(laneId).filter((run) => run.origin === 'execution');
  return (
    <section className="trajectory-col" data-testid="trajectory-col">
      <h3>{name}</h3>
      {runs.every((run) => run.blocks.length === 0) && (
        <p className="faint" data-testid="trajectory-empty">
          No events yet
        </p>
      )}
      {runs.map((run) => (
        <div key={run.runId} className="trajectory-compact">
          {run.blocks.map((b) => (
            <BlockRow key={b.kind === 'tool' ? b.callId : b.id} block={b} run={run} />
          ))}
          <RunFooter run={run} />
        </div>
      ))}
    </section>
  );
}

export function TrajectoriesTab({ data }: { data: CompareResult }) {
  return (
    <div
      className="diff-grid"
      style={{ gridTemplateColumns: `repeat(${data.lanes.length}, minmax(360px, 1fr))` }}
    >
      {data.lanes.map((l) => (
        <Column key={l.laneId} laneId={l.laneId} name={l.name} />
      ))}
    </div>
  );
}
