'use client';
import { Square } from 'lucide-react';
import { useState } from 'react';
import { useShortcutHints } from '@/ui/components/chrome/useShortcutHints';
import { MAX_TURNS_LIMIT, readableError } from '@/ui/components/lane/readableError';
import { useMissionStore } from '@/ui/store/missionStore';
import { useLaneActive, useLaneStatus } from '@/ui/store/selectors';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';

/**
 * Keeps the field inside the server's range as it is typed: digits only, nothing below 1, and
 * anything above 200 pinned to 200 rather than silently rejected on submit.
 */
function clampMaxTurns(raw: string): string {
  const digits = raw.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
  if (digits === '') return '';
  return String(Math.min(MAX_TURNS_LIMIT, Math.max(1, Number(digits))));
}

export function Composer({
  laneId,
  archived = false,
  context,
}: {
  laneId: string;
  /** An archived session is read-only until it is reopened; the server refuses new work either way. */
  archived?: boolean;
  /**
   * Which pane the body is showing, when it is not the conversation.
   *
   * The composer always talks to *this session*, and says so — but for the right reason. Under a
   * delegate's trajectory the thing to explain is that a native subagent has no conversation of its
   * own; under the outputs it is that a reply asks the session to change these files; under a
   * wrap-up's captured inputs it is that the package is a record and replying will not re-capture
   * it. The copy used to be the delegate sentence in all of them, which told somebody reading a
   * file about a child that was not on screen.
   */
  context?: 'agent' | 'outputs' | 'inputs';
}) {
  const hints = useShortcutHints();
  // The draft lives in the store, keyed by session: this component unmounts whenever a delegate is
  // opened or another session is visited, and local state took half-written prompts with it.
  const prompt = useMissionStore((s) => s.draftByLane[laneId] ?? '');
  const setPrompt = (text: string) => useMissionStore.getState().setDraft(laneId, text);
  const [maxTurns, setMaxTurns] = useState<string>('');
  const running = useLaneStatus(laneId) === 'running';
  const active = useLaneActive(laneId);
  // What the server would actually resume: a run this lane executed, with a result, that is not a
  // replay or an import (`resumable` on the run summary). Trusting any stored session id is how a
  // follow-up ended up resuming an imported recording's id and failing on the CLI's UUID check —
  // and a replay now keeps the session id it copied, so a session id says even less than it did.
  const hasSession = useMissionStore((s) =>
    (s.runsByLane[laneId] ?? []).some((id) => s.runs[id]?.resumable === true),
  );
  const utils = trpc.useUtils();
  const onDone = () => {
    setPrompt('');
    utils.runs.list.invalidate({ laneId });
  };
  const start = trpc.runs.start.useMutation({ onSuccess: onDone });
  const resume = trpc.runs.resume.useMutation({ onSuccess: onDone });
  const cancel = trpc.runs.cancel.useMutation({
    onSuccess: (res) => {
      if (!res.cancelled)
        useToasts.getState().push({ kind: 'info', title: 'Nothing to stop — the run had already ended' });
    },
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not stop the run', text: e.message }),
  });
  // Continuing this conversation is what the composer is for; a conversation without the previous
  // turns is a new session, which is its own control and makes its own row in the list.
  const willResume = hasSession;
  const busy = running || archived || start.isPending || resume.isPending;
  // Nothing typed is nothing to send: the button used to look ready and then do nothing.
  const empty = prompt.trim() === '';
  const submit = () => {
    const text = prompt.trim();
    if (!text || busy) return;
    const options = maxTurns ? { maxTurns: Number(maxTurns) } : {};
    if (willResume) resume.mutate({ laneId, prompt: text, ...options });
    else start.mutate({ laneId, prompt: text, ...options });
  };
  const error = start.error ?? resume.error;
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      data-testid="composer"
      data-context={context}
    >
      {context && (
        <p className="faint composer-context" data-testid="composer-context" data-context={context}>
          {context === 'agent'
            ? 'This replies to the session. A delegate is part of it and has no conversation of its own.'
            : context === 'inputs'
              ? 'This replies to the session. What it was given was captured once and does not change.'
              : 'This replies to the session — ask it to change or add to these files.'}
        </p>
      )}
      <textarea
        className="composer-input"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            submit();
          }
        }}
        disabled={archived}
        placeholder={
          archived
            ? 'Archived — reopen this session to continue it.'
            : context
              ? 'Reply to this session…'
              : willResume
                ? 'Follow up in this session…'
                : 'Ask the agent to do something in this folder…'
        }
        rows={2}
        data-testid="composer-input"
      />
      <div className="composer-row">
        <label
          className="check faint max-turns"
          title={`Stop the agent after this many turns, 1 to ${MAX_TURNS_LIMIT} (useful to demonstrate a failing run)`}
        >
          <span className="check-text">max turns</span>{' '}
          <input
            className="input input-xs mono"
            value={maxTurns}
            onChange={(e) => setMaxTurns(clampMaxTurns(e.target.value))}
            placeholder="∞"
            inputMode="numeric"
            aria-label={`Max turns, 1 to ${MAX_TURNS_LIMIT}`}
            data-testid="composer-max-turns"
          />
        </label>
        <span className="spacer" />
        {error && (
          <span className="error" role="alert">
            {readableError(error.message)}
          </span>
        )}
        {active && (
          <button
            type="button"
            className="btn btn-danger"
            // The run that is actually running, not the newest row in the lane.
            onClick={() => cancel.mutate({ runId: active.runId })}
            data-testid="composer-cancel"
          >
            <Square size={12} /> Stop
          </button>
        )}
        <button
          type="submit"
          className="btn btn-primary"
          disabled={busy || empty}
          aria-label={willResume ? 'Follow up' : 'Run'}
          data-testid="composer-submit"
        >
          {willResume ? 'Follow up' : 'Run'} <kbd className="kbd">{hints.submit}</kbd>
        </button>
      </div>
    </form>
  );
}
