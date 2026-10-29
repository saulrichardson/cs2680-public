'use client';
import { X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import type { RunStatus } from '@/core/types';
import { formatDuration, formatUsd } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { type Toast, useToasts } from '@/ui/store/toasts';

/** How long a toast stays up. The store sweeps its own list sooner; this component owns what is on
 * screen, because a banner offering an `Open` link has to outlast the time it takes to notice it
 * (the fan-out toast was gone in about six seconds in QA). */
const DWELL: Record<Toast['kind'], number> = { info: 12_000, success: 12_000, error: 15_000 };
const TICK = 250;

/**
 * Turns run status transitions into toasts. A lane that finishes while the operator is reading
 * another one said nothing at all before; now it does, with its numbers and a way straight to it.
 *
 * Transitions only: a status first seen as `finished` (every run on the board after a reload) is
 * not news. The previous status per run lives in a ref here rather than in the store, which stays
 * a pure projection of the event stream.
 */
function useRunLifecycleToasts(): void {
  const runs = useMissionStore((s) => s.runs);
  const lanes = useMissionStore((s) => s.lanes);
  const seen = useRef(new Map<string, RunStatus>());
  useEffect(() => {
    for (const run of Object.values(runs)) {
      const previous = seen.current.get(run.runId);
      seen.current.set(run.runId, run.status);
      if (previous === undefined || previous === run.status) continue;
      // The visible session already shows its outcome. A second notice can cover its next action
      // and stay there while the pointer hovers over it. Notify only for work outside the view.
      const visible = [...document.querySelectorAll<HTMLElement>('[data-lane-id]')].some(
        (element) => element.dataset.laneId === run.laneId && element.getClientRects().length > 0,
      );
      if (visible) continue;
      const name = lanes[run.laneId]?.name ?? 'lane';
      const href = `/lanes/${run.laneId}`;
      if (run.status === 'finished') {
        const numbers = run.numbers
          ? `${formatUsd(run.numbers.costUsd)} · ${formatDuration(run.numbers.durationMs)}`
          : undefined;
        useToasts.getState().push({ kind: 'success', title: `${name} finished`, text: numbers, href });
      } else if (run.status === 'failed') {
        useToasts.getState().push({
          kind: 'error',
          title: `${name} failed`,
          text: run.error?.message,
          href,
        });
      }
    }
  }, [runs, lanes]);
}

export function Toasts() {
  const storeToasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  useRunLifecycleToasts();
  // What is on screen, and how much longer each one has. Kept out of render state so the countdown
  // does not re-render the app four times a second; only an expiry does.
  const [shown, setShown] = useState<Toast[]>([]);
  const left = useRef(new Map<number, number>());
  const retired = useRef(new Set<number>());
  const paused = useRef(false);

  useEffect(() => {
    const added = storeToasts.filter((t) => !left.current.has(t.id) && !retired.current.has(t.id));
    if (added.length === 0) return;
    for (const t of added) left.current.set(t.id, DWELL[t.kind]);
    setShown((list) => [...list, ...added]);
  }, [storeToasts]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (paused.current) return;
      const expired: number[] = [];
      for (const [id, remaining] of left.current) {
        if (remaining <= TICK) {
          expired.push(id);
          left.current.delete(id);
          retired.current.add(id);
        } else left.current.set(id, remaining - TICK);
      }
      if (expired.length > 0) setShown((list) => list.filter((t) => !expired.includes(t.id)));
    }, TICK);
    return () => clearInterval(timer);
  }, []);

  const close = (id: number) => {
    left.current.delete(id);
    retired.current.add(id);
    setShown((list) => list.filter((t) => t.id !== id));
    dismiss(id);
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: the region only freezes its own countdown while it is read; every toast's actions are real buttons and links.
    <div
      className="toasts"
      aria-live="polite"
      onMouseEnter={() => {
        paused.current = true;
      }}
      onMouseLeave={() => {
        paused.current = false;
      }}
      onFocus={() => {
        paused.current = true;
      }}
      onBlur={() => {
        paused.current = false;
      }}
    >
      {shown.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} data-testid="toast">
          <div className="toast-body">
            <strong>{t.title}</strong>
            {t.text && <span className="muted">{t.text}</span>}
            {t.href && (
              <Link href={t.href} className="toast-link" onClick={() => close(t.id)}>
                Open
              </Link>
            )}
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => close(t.id)}
            aria-label="Dismiss"
            data-testid="toast-close"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
