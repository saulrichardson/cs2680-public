'use client';
import { useEffect, useMemo, useState } from 'react';
import { deriveTimeline, timelineSpan } from '@/core/derive';
import type { CompareResult, TimelineBar } from '@/core/types';
import { formatDuration } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { callKey } from '@/ui/store/rows';
import { laneHueStyle } from '@/ui/theme/laneHue';

export function TimelineTab({ data }: { data: CompareResult }) {
  const runs = useMissionStore((s) => s.runs);
  const [now, setNow] = useState(() => Date.now());
  const anyRunning = data.lanes.some((l) => l.runId && runs[l.runId]?.status === 'running');
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [anyRunning]);
  const rows = useMemo(
    () =>
      data.lanes.map((lane) => {
        const view = lane.runId ? runs[lane.runId] : undefined;
        const bars = view ? deriveTimeline(view, now) : [];
        // `timelineSpan` owns the axis rule: a live run runs to the wall clock, a terminal one to
        // its own `endedAt`, and either way the span is widened to contain every bar. A replay
        // re-bases its event stamps on the new run's start while copying the recording's duration
        // into `endedAt`, so a recorded result can sit after the declared end.
        const span = view ? timelineSpan(view, bars, now) : { start: 0, end: 0 };
        return { lane, view, bars, start: span.start, end: span.end };
      }),
    [data.lanes, runs, now],
  );
  // A row whose run hasn't hydrated yet (e.g. the first paint after a reload) reports start 0;
  // excluding those keeps t0/t1 finite instead of collapsing to Infinity/NaN once every row is
  // unhydrated, which would otherwise give every tick the same "NaN" key and a NaN% position.
  const hydratedRows = rows.filter((r) => r.start !== 0);
  const t0 = hydratedRows.length ? Math.min(...hydratedRows.map((r) => r.start)) : Date.now();
  const t1 = hydratedRows.length ? Math.max(...hydratedRows.map((r) => r.end || 0), t0 + 1000) : t0 + 1000;
  const span = Math.max(1, t1 - t0);
  const pct = (t: number) => `${((t - t0) / span) * 100}%`;
  const ticks = Array.from({ length: 6 }, (_, i) => t0 + (span * i) / 5);
  if (rows.every((r) => r.bars.length === 0))
    return (
      <div className="timeline" data-testid="compare-timeline">
        <p className="faint" data-testid="timeline-empty">
          Waiting for the first tool call…
        </p>
      </div>
    );
  // A lane's candidate can be several executions (a first attempt and the follow-ups that continued
  // it), and the strip's figures are totals across all of them. This view draws one — the latest —
  // because the bars are one run's own clock. Saying so is the difference between a partial view
  // and a misleading one.
  const multipleAttempts = data.lanes.some((l) => l.attempts.length > 1);
  return (
    <div className="timeline" data-testid="compare-timeline">
      {multipleAttempts && (
        <p className="faint compare-attempts" data-testid="timeline-scope">
          Showing each lane's latest execution. Some lanes ran more than once; the figures above are totals
          across every attempt.
        </p>
      )}
      <div className="timeline-axis">
        {ticks.map((t, i) => (
          <span
            key={t}
            style={{ left: pct(t) }}
            className="tick mono"
            data-edge={i === 0 ? 'start' : i === ticks.length - 1 ? 'end' : undefined}
          >
            {formatDuration(t - t0)}
          </span>
        ))}
      </div>
      {rows.map(({ lane, bars, start, end }) => (
        <div
          key={lane.laneId}
          className="timeline-row"
          style={laneHueStyle(lane.groupIndex)}
          data-testid="timeline-row"
        >
          <div className="timeline-label">
            <strong>{lane.name}</strong>
            <span className="faint mono">{formatDuration(end - start)}</span>
          </div>
          <div className="timeline-track" data-testid="timeline-track">
            {bars.map((b: TimelineBar) => (
              <div
                // The call id alone is the CLI's, unique only within its own run; the pair is what
                // identifies a bar (see `callKey`).
                key={callKey({ runId: lane.runId ?? '', callId: b.callId })}
                className={`timeline-bar cls-${b.toolClass} status-${b.status}`}
                data-testid="timeline-bar"
                style={{
                  left: pct(b.start),
                  width: `max(2px, calc(${pct(b.end)} - ${pct(b.start)}))`,
                  top: 4 + b.depth * 14,
                }}
                title={`${b.name} · ${formatDuration(b.end - b.start)}${
                  b.status === 'pending' ? ' (no result)' : ''
                }${b.depth ? ' (subagent)' : ''}`}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="legend">
        {(['search', 'mutate', 'execute', 'delegate', 'network', 'other'] as const).map((c) => (
          <span key={c} className="legend-item">
            <span className={`legend-swatch cls-${c}`} /> {c}
          </span>
        ))}
      </div>
    </div>
  );
}
