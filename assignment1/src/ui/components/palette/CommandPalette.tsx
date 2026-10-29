'use client';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import type { LaneDto } from '@/core/types';
import { useDialogFocus } from '@/ui/components/chrome/useDialogFocus';
import { useMissionStore } from '@/ui/store/missionStore';
import { usePalette } from '@/ui/store/palette';
import { useToasts } from '@/ui/store/toasts';
import { laneHueIndex, laneHueStyle } from '@/ui/theme/laneHue';
import { trpc } from '@/ui/trpc/client';

interface Action {
  id: string;
  label: string;
  hint?: string;
  /** Set on the Focus rows, so a row wears the same hue as the lane it opens. */
  laneIndex?: number;
  run(): void;
}

export function CommandPalette() {
  const router = useRouter();
  const { open, setOpen, request } = usePalette();
  const [q, setQ] = useState('');
  const [cursor, setCursor] = useState(0);
  const laneOrder = useMissionStore((s) => s.laneOrder);
  const laneMap = useMissionStore((s) => s.lanes);
  const lanes = useMemo(
    () => laneOrder.map((id) => laneMap[id]).filter((l): l is LaneDto => Boolean(l)),
    [laneOrder, laneMap],
  );
  const runs = useMissionStore((s) => s.runs);
  const cancel = trpc.runs.cancel.useMutation({
    onSuccess: (res) => {
      if (!res.cancelled)
        useToasts.getState().push({ kind: 'info', title: 'Nothing to stop — the run had already ended' });
    },
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not stop the run', text: e.message }),
  });
  // The confirmation belongs to the reply, not to the click: a replay the server refuses used to
  // announce itself anyway.
  const replay = trpc.replay.start.useMutation({
    onSuccess: () => useToasts.getState().push({ kind: 'info', title: 'Replaying' }),
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not start the replay', text: e.message }),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(!open);
        setQ('');
        setCursor(0);
      }
      if (e.key === 'Escape' && open) setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, setOpen]);

  const actions = useMemo<Action[]>(() => {
    // Fan out and Compare are deliberately absent. They are a race capability, not part of the
    // ordinary way this console is used, and offering them here would put them back into primary
    // navigation through the side door. Import and Replay stay: both are about inspecting work
    // that was actually recorded.
    const list: Action[] = [
      {
        id: 'start',
        label: 'Start a session',
        hint: 'One prompt against one folder',
        run: () => {
          router.push('/');
          setOpen(false);
        },
      },
      {
        id: 'import',
        label: 'Import a recorded events.jsonl',
        hint: 'Render a saved stream as a run',
        run: () => request({ kind: 'import' }),
      },
    ];
    for (const [position, lane] of lanes.entries()) {
      list.push({
        id: `focus-${lane.id}`,
        label: `Open ${lane.name}`,
        hint: lane.cwd,
        // the same hue the lane wears on the board and in Compare
        laneIndex: laneHueIndex(lane, position),
        run: () => {
          router.push(`/lanes/${lane.id}`);
          setOpen(false);
        },
      });
    }
    for (const run of Object.values(runs)) {
      if (run.status === 'running')
        list.push({
          id: `stop-${run.runId}`,
          label: `Stop: ${run.prompt.slice(0, 50)}`,
          hint: 'Stop this run',
          run: () => {
            cancel.mutate({ runId: run.runId });
            setOpen(false);
          },
        });
      else if (run.blocks.length)
        list.push({
          id: `replay-${run.runId}`,
          label: `Replay at 4×: ${run.prompt.slice(0, 50)}`,
          run: () => {
            replay.mutate({ sourceRunId: run.runId, speed: '4x' });
            setOpen(false);
          },
        });
    }
    return list;
  }, [lanes, runs, router, setOpen, request, cancel, replay]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? actions.filter((a) => `${a.label} ${a.hint ?? ''}`.toLowerCase().includes(needle))
      : actions;
  }, [actions, q]);

  // Only the first 12 matches are ever rendered — navigation, clamping, and Enter must all agree
  // on that same slice, or an arrow key / Enter could act on a row that isn't on screen.
  const visible = useMemo(() => filtered.slice(0, 12), [filtered]);

  // The list can shrink out from under the cursor (a query narrows the matches, or a running run
  // this list was tracking finishes and drops its "Stop" action) — keep the cursor in range.
  useEffect(() => {
    setCursor((c) => Math.min(c, Math.max(0, visible.length - 1)));
  }, [visible.length]);

  if (!open) return null;
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: scrim closes on click; Escape is handled globally above.
    <div className="scrim scrim-top" onClick={() => setOpen(false)} role="presentation">
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: onClick only stops the scrim's close-click from bubbling; Escape is handled globally above. */}
      <div
        className="palette"
        role="dialog"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
        data-testid="palette"
      >
        <input
          className="palette-input"
          // biome-ignore lint/a11y/noAutofocus: the palette is a keyboard-summoned overlay (⌘K); autofocus is the point.
          autoFocus
          value={q}
          placeholder="Type a command…"
          aria-label="Command palette"
          role="combobox"
          aria-expanded
          aria-controls="palette-list"
          aria-autocomplete="list"
          aria-activedescendant={visible[cursor] ? `palette-option-${cursor}` : undefined}
          onChange={(e) => {
            setQ(e.target.value);
            setCursor(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setCursor((c) => Math.min(visible.length - 1, c + 1));
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault();
              setCursor((c) => Math.max(0, c - 1));
            }
            if (e.key === 'Enter') {
              e.preventDefault();
              visible[cursor]?.run();
            }
          }}
          data-testid="palette-input"
        />
        {/* biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: the input above is a combobox, which needs a listbox to control; a <ul> of options is the list this is. */}
        <ul className="palette-list" id="palette-list" role="listbox" aria-label="Commands">
          {visible.map((a, i) => (
            <li key={a.id} role="presentation">
              <button
                type="button"
                role="option"
                id={`palette-option-${i}`}
                aria-selected={i === cursor}
                className={`palette-item${i === cursor ? ' active' : ''}`}
                style={a.laneIndex === undefined ? undefined : laneHueStyle(a.laneIndex)}
                data-lane={a.laneIndex === undefined ? undefined : a.laneIndex}
                onMouseEnter={() => setCursor(i)}
                onClick={a.run}
                data-testid="palette-item"
              >
                <span>{a.label}</span>
                {a.hint && <span className="faint truncate">{a.hint}</span>}
              </button>
            </li>
          ))}
          {visible.length === 0 && (
            <li className="faint palette-empty" role="presentation">
              No matching commands
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

export function ImportDialog({ onClose }: { onClose: () => void }) {
  const dialog = useDialogFocus<HTMLDivElement>();
  const laneOrder = useMissionStore((s) => s.laneOrder);
  const laneMap = useMissionStore((s) => s.lanes);
  const lanes = useMemo(
    () => laneOrder.map((id) => laneMap[id]).filter((l): l is LaneDto => Boolean(l)),
    [laneOrder, laneMap],
  );
  const [laneId, setLaneId] = useState(lanes[0]?.id ?? '');
  const [contents, setContents] = useState('');
  const [label, setLabel] = useState('events.jsonl');
  const imp = trpc.replay.import.useMutation({
    onSuccess: () => {
      useToasts.getState().push({ kind: 'success', title: 'Imported recording' });
      onClose();
    },
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not import the recording', text: e.message }),
  });
  useEffect(() => {
    if (!laneId && lanes[0]) setLaneId(lanes[0].id);
  }, [lanes, laneId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: scrim closes on click; Escape is handled by this dialog's own keydown effect above (the palette that opened it already closed itself via request()).
    <div className="scrim" onClick={onClose} role="presentation">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        ref={dialog.ref}
        onKeyDown={dialog.onKeyDown}
        onClick={(e) => e.stopPropagation()}
        data-testid="import-dialog"
      >
        <h2 id="import-title">Import a recording</h2>
        {lanes.length === 0 ? (
          <p className="muted" data-testid="import-no-lanes">
            Create a lane first — an import needs somewhere to land.
          </p>
        ) : (
          <label className="field">
            <span>Into lane</span>
            <select
              className="input"
              value={laneId}
              onChange={(e) => setLaneId(e.target.value)}
              data-testid="import-lane"
            >
              {lanes.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>File</span>
          <input
            type="file"
            accept=".jsonl,.txt,application/x-ndjson"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) {
                setLabel(f.name);
                setContents(await f.text());
              }
            }}
          />
        </label>
        <label className="field">
          <span>Or paste the stream</span>
          <textarea
            className="input mono"
            rows={6}
            value={contents}
            onChange={(e) => setContents(e.target.value)}
            data-testid="import-contents"
            placeholder="Paste the contents of an events.jsonl file"
          />
        </label>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!laneId || contents.trim().length === 0 || imp.isPending}
            onClick={() => imp.mutate({ laneId, contents, label })}
            data-testid="import-start"
          >
            Import
          </button>
        </div>
      </div>
    </div>
  );
}
