'use client';
import { MoreHorizontal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { RunView } from '@/core/types';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';

export function RunMenu({ run }: { run: RunView }) {
  const [open, setOpen] = useState(false);
  // The run list is virtualized (LaneRuns.tsx): every row is `position: absolute` with an inline
  // `transform: translateY(...)`, which makes each row its own stacking context. A `z-index` set
  // inside one row's subtree can never rise above a *different* row — so an absolutely-positioned
  // dropdown anchored to the button would end up painted under whichever row happens to come later
  // in the list. Portal the menu to `document.body` (a sibling of every row's stacking context) and
  // position it from the trigger's measured screen position instead.
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const replay = trpc.replay.start.useMutation({
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not start the replay', text: e.message }),
  });
  const cancel = trpc.runs.cancel.useMutation({
    onSuccess: (res) => {
      if (!res.cancelled)
        useToasts.getState().push({ kind: 'info', title: 'Nothing to stop — the run had already ended' });
    },
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not stop the run', text: e.message }),
  });
  // Where the trigger sat when the menu opened. The menu is `position: fixed` at that measurement,
  // so once the trigger has actually moved the menu is pointing at the wrong row.
  const openAt = useRef<{ top: number; left: number } | null>(null);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target) || menuRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      close();
      triggerRef.current?.focus();
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    // The lane scrolls (auto-follow during a live run, the user scrolling to read an earlier one)
    // independently of any click; a fixed-position menu anchored to a now-stale rect would float
    // over unrelated rows, so a scroll closes it rather than trying to re-track it. `scroll` doesn't
    // bubble, so this only sees anything via the capture phase — but capture also delivers a
    // no-op "scroll" some browsers fire right after a click focuses an off-screen-ish trigger
    // (observed: a real click on `run-menu`, immediately followed by one `scroll` event that moved
    // nothing). Ask the only question that matters instead of trying to recognize that event: has
    // the trigger moved since the menu opened? A no-op scroll leaves it exactly where it was.
    const onScroll = () => {
      const from = openAt.current;
      const rect = ref.current?.getBoundingClientRect();
      if (!from || !rect) return;
      if (Math.abs(rect.top - from.top) >= 1 || Math.abs(rect.left - from.left) >= 1) close();
    };
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', close);
    };
  }, [open]);
  const toggle = () => {
    if (!open && ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, right: document.documentElement.clientWidth - rect.right });
      openAt.current = { top: rect.top, left: rect.left };
    }
    setOpen((o) => !o);
  };
  // The menu portals in on the commit after the click, so its first item can only be focused once
  // it exists. Arrow keys then cycle inside it (see the menu's own onKeyDown).
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() =>
      menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus(),
    );
    return () => cancelAnimationFrame(frame);
  }, [open]);
  const go = (speed: 'instant' | '1x' | '4x') => {
    replay.mutate({ sourceRunId: run.runId, speed });
    setOpen(false);
  };
  return (
    <div className="run-menu" ref={ref}>
      <button
        type="button"
        className="btn btn-ghost"
        aria-label="Run actions"
        aria-haspopup="menu"
        aria-expanded={open}
        ref={triggerRef}
        onClick={toggle}
        data-testid="run-menu"
      >
        <MoreHorizontal size={14} />
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            className="menu"
            role="menu"
            ref={menuRef}
            style={{ top: pos.top, right: pos.right }}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
              e.preventDefault();
              const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
              if (items.length === 0) return;
              const at = items.indexOf(document.activeElement as HTMLElement);
              const next =
                e.key === 'ArrowDown' ? (at + 1) % items.length : at <= 0 ? items.length - 1 : at - 1;
              items.at(next)?.focus();
            }}
          >
            {/* A run still in flight has nothing to replay — `replay.start` refuses a non-terminal
                source with PRECONDITION_FAILED — so the group is hidden rather than offered and
                rejected, matching what the command palette already does. */}
            {run.status !== 'running' && (
              <>
                <div className="menu-label" role="presentation">
                  Replay
                </div>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => go('instant')}
                  data-testid="menu-replay-instant"
                >
                  Instantly
                </button>
                <button type="button" role="menuitem" onClick={() => go('1x')} data-testid="menu-replay-1x">
                  At recorded speed
                </button>
                <button type="button" role="menuitem" onClick={() => go('4x')} data-testid="menu-replay-4x">
                  At 4×
                </button>
              </>
            )}
            <div className="menu-label" role="presentation">
              Run
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={async () => {
                if (run.sessionId && typeof navigator !== 'undefined' && navigator.clipboard) {
                  try {
                    await navigator.clipboard.writeText(run.sessionId);
                  } catch {
                    // clipboard unavailable or denied: nothing else to do here
                  }
                }
                setOpen(false);
              }}
              disabled={!run.sessionId}
            >
              Copy session id
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                const a = document.createElement('a');
                a.href = `/api/export/${run.runId}`;
                a.download = `${run.runId}.jsonl`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setOpen(false);
              }}
            >
              Download events.jsonl
            </button>
            {run.status === 'running' && (
              <button
                type="button"
                role="menuitem"
                className="danger"
                onClick={() => {
                  cancel.mutate({ runId: run.runId });
                  setOpen(false);
                }}
              >
                Stop
              </button>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
