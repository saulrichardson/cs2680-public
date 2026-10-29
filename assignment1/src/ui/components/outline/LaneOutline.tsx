'use client';
import { useMemo } from 'react';
import { deriveOutline } from '@/core/derive';
import type { CallRef } from '@/core/ref';
import { useLaneRuns, useMissionStore } from '@/ui/store/missionStore';
import { Outline, type OutlineGroup } from './Outline';

/**
 * The outline for a session's own conversation: every run in the lane, oldest first.
 *
 * `LaneRuns` does the scrolling, because it owns the virtualizer and the outline lists calls whose
 * cards are routinely absent from the document — scrolled out of the mounted window, or folded
 * inside a subagent group — which is more than `getElementById().scrollIntoView()` can reach.
 */
export function LaneOutline({
  laneId,
  variant,
  onNavigate,
}: {
  laneId: string;
  variant: 'rail' | 'sidebar';
  onNavigate?(): void;
}) {
  const runs = useLaneRuns(laneId);
  const active = useMissionStore((s) => s.activeCallByLane[laneId] ?? null);
  const selected = useMissionStore((s) => s.selection.call);
  const groups = useMemo<OutlineGroup[]>(
    () =>
      runs.map((run) => ({
        key: run.runId,
        runId: run.runId,
        label: run.prompt,
        status: run.status,
        items: deriveOutline(run, run.cwd),
      })),
    [runs],
  );
  const jump = (ref: CallRef) => {
    useMissionStore.getState().requestJump(laneId, ref);
    useMissionStore.getState().select(laneId, ref);
    onNavigate?.();
  };
  return <Outline groups={groups} variant={variant} active={active} selected={selected} onJump={jump} />;
}
