'use client';
import { useMemo } from 'react';
import { aggregateNumbers } from '@/core/derive';
import type { CompareLane, CompareResult } from '@/core/types';
import { formatDurationCompact, formatUsd } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { LaneSummaryCard } from './LaneSummaryCard';

/**
 * The winning figure *as displayed*, or null when there is no winner to point at.
 *
 * Three lanes that all cost $0.86 are not a podium — the QA pass saw every lane's cost marked green
 * — and two lanes that differ by a thousandth of a cent read as identical, so the comparison is made
 * on the text the operator can actually see and dropped entirely when it is a draw.
 *
 * A lane whose figure is only a lower bound is excluded outright, not compared on what it happened
 * to report: calling the lane with an unreported cost the cheapest is precisely the wrong answer to
 * the question the operator is here to ask.
 */
function bestText(
  candidates: { value: number | undefined; complete: boolean }[],
  format: (n: number) => string,
): string | null {
  const present = candidates
    .filter((c) => c.complete && c.value !== undefined && Number.isFinite(c.value))
    .map((c) => c.value as number);
  if (present.length === 0) return null;
  const text = format(Math.min(...present));
  return present.filter((v) => format(v) === text).length > 1 ? null : text;
}

export function CompareStrip({ data, onChanged }: { data: CompareResult; onChanged: () => void }) {
  const runs = useMissionStore((s) => s.runs);
  // Merge the live store's numbers into each lane once, here, so the "best" marker and the figures
  // each LaneSummaryCard displays are computed from the same basis. Without this, `best` (from the
  // server snapshot) and the card (from the live store) could disagree for up to one poll interval
  // during a race, landing the green marker on the lane that was merely slower to poll.
  //
  // The merge happens *inside* the candidate's attempts rather than over the top of them: a lane's
  // figures are totals across everything it executed, and taking the live run's numbers whole would
  // silently drop the earlier attempts a follow-up race lane is made of.
  const lanes: CompareLane[] = useMemo(
    () =>
      data.lanes.map((lane) => {
        const attempts = lane.attempts.map((attempt) => {
          const live = runs[attempt.runId];
          if (!live) return attempt;
          return {
            ...attempt,
            numbers: live.numbers ?? attempt.numbers,
            endedAt: live.endedAt ?? attempt.endedAt,
          };
        });
        const wall = attempts
          .filter((a) => a.endedAt !== undefined)
          .map((a) => Math.max(0, (a.endedAt as number) - a.startedAt));
        const totals = aggregateNumbers(attempts.map((a) => a.numbers));
        return {
          ...lane,
          attempts,
          numbers: totals.numbers ?? lane.numbers,
          metricGaps: totals.gaps,
          // An attempt with no end yet is a gap in the wall time too.
          wallMs: wall.length ? wall.reduce((sum, ms) => sum + ms, 0) : lane.wallMs,
        };
      }),
    [data.lanes, runs],
  );
  const best = {
    cost: bestText(
      lanes.map((l) => ({ value: l.numbers?.costUsd, complete: l.metricGaps.costUsd === 0 })),
      formatUsd,
    ),
    wall: bestText(
      lanes.map((l) => ({
        value: l.wallMs,
        complete: l.attempts.every((a) => a.endedAt !== undefined) && l.attempts.length > 0,
      })),
      formatDurationCompact,
    ),
  };
  return (
    <div className="compare-strip" data-testid="compare-strip">
      {lanes.map((lane) => {
        // The badge belongs to the lane whose *history* holds the kept run, not only the lane whose
        // latest execution it happens to be — otherwise a follow-up made the operator's choice
        // vanish rather than showing it as overtaken.
        const kept = data.keptRunId !== null && lane.attempts.some((a) => a.runId === data.keptRunId);
        return (
          <LaneSummaryCard
            key={lane.laneId}
            lane={lane}
            groupId={data.groupId}
            kept={kept}
            superseded={kept && data.keptSuperseded}
            best={best}
            onChanged={onChanged}
          />
        );
      })}
    </div>
  );
}
