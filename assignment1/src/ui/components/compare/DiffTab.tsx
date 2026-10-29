'use client';
import type { CompareResult } from '@/core/types';
import { HunkView } from '@/ui/components/diff/HunkView';
import { useMissionStore } from '@/ui/store/missionStore';

export function DiffTab({ data }: { data: CompareResult }) {
  const runs = useMissionStore((s) => s.runs);
  return (
    <div
      className="diff-grid"
      style={{ gridTemplateColumns: `repeat(${data.lanes.length}, minmax(320px, 1fr))` }}
    >
      {data.lanes.map((lane) => {
        // Every execution that produced this worktree, for the same reason the figures are totals:
        // the edits an agent reported span all of its attempts, not only the latest one.
        const toolPatches = lane.attempts.flatMap((attempt) =>
          Object.values(runs[attempt.runId]?.callsById ?? {}).flatMap((c) => c.patches),
        );
        return (
          <section key={lane.laneId} className="diff-lane" data-testid="diff-lane">
            <h3>
              {lane.name} <span className="faint">changes since {data.baseCommit.slice(0, 7)}</span>
            </h3>
            {lane.files.length === 0 ? (
              <p className="faint">
                {toolPatches.length > 0
                  ? "Nothing changed on disk in this worktree yet — the agent's tools reported these edits."
                  : 'Nothing changed on disk in this worktree.'}
              </p>
            ) : (
              <ul className="file-list">
                {lane.files.map((f) => (
                  <li key={f.path} className="mono">
                    <span className="truncate">{f.path}</span>
                    <span className="diff-add">+{f.added}</span>
                    <span className="diff-del">−{f.removed}</span>
                  </li>
                ))}
              </ul>
            )}
            {lane.patches.map((p) => (
              <HunkView key={`disk-${p.filePath}`} patch={p} cwd={lane.worktreePath} />
            ))}
            {lane.patches.length === 0 && toolPatches.length > 0 && (
              <>
                <h4 className="faint">Edits reported by the agent's tools</h4>
                {toolPatches.map((p, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: tool patches are a static, ordered render for this lane, never reordered/filtered.
                  <HunkView key={`tool-${i}-${p.filePath}`} patch={p} cwd={lane.worktreePath} />
                ))}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
