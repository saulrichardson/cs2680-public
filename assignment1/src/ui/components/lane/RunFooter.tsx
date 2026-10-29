'use client';
import { Check, Copy, Loader2, OctagonX, Square } from 'lucide-react';
import { useState } from 'react';
import { isPlayback, type RunView } from '@/core/types';
import { formatCount, formatDuration, formatTokens, formatUsd, shortId } from '@/ui/format';

export function RunFooter({ run }: { run: RunView }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!run.sessionId || typeof navigator === 'undefined' || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(run.sessionId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard unavailable or denied: leave the id visible in the title
    }
  };
  const wall = run.endedAt ? run.endedAt - run.startedAt : undefined;
  // A replay's `endedAt` is `startedAt + (source.endedAt - source.startedAt)` and an import's comes
  // from the recorded timestamps (src/server/replay.ts), so for both the "wall clock" here is the
  // original run's, not how long this one took on screen. Say so rather than let it read as a lie.
  // From the run's own provenance, not from reading its prompt.
  const recorded = isPlayback(run.origin);
  const Icon =
    run.status === 'finished'
      ? Check
      : run.status === 'failed'
        ? OctagonX
        : run.status === 'cancelled'
          ? Square
          : Loader2;
  // A run the operator stopped is not a failure: it says so in its own words, keeps the numbers the
  // CLI did report, and never shows an error box — the interrupt diagnostic the CLI prints on SIGINT
  // is bookkeeping, not something that happened to the user.
  const cancelled = run.status === 'cancelled';
  const label =
    run.status === 'running'
      ? (run.activity ??
        (run.thinking ? `Thinking… ${formatTokens(run.thinking.estimatedTokens)} tokens` : 'Working…'))
      : cancelled
        ? 'Stopped by you'
        : run.status;
  return (
    <div className={`run-footer run-footer-${run.status}`} data-testid="run-footer" data-status={run.status}>
      <span className="run-footer-status">
        <Icon size={13} className={run.status === 'running' ? 'spin' : ''} />{' '}
        <span className="run-footer-label">{label}</span>
      </span>
      {run.numbers && (
        <span
          className="run-numbers mono"
          title={
            wall
              ? `reported ${formatDuration(run.numbers.durationMs)} · wall clock ${formatDuration(wall)}${
                  recorded ? " (the recording's wall time)" : ''
                }`
              : undefined
          }
        >
          <span data-testid="run-cost">{formatUsd(run.numbers.costUsd)}</span> ·{' '}
          <span data-testid="run-duration">{formatDuration(run.numbers.durationMs)}</span> ·{' '}
          <span data-testid="run-turns">
            {run.numbers.numTurns === undefined ? '– turns' : formatCount(run.numbers.numTurns, 'turn')}
          </span>
        </span>
      )}
      {run.usage && (
        <span
          className="faint mono"
          title={`in ${formatTokens(run.usage.inputTokens)} · cache read ${formatTokens(run.usage.cacheReadTokens)} · cache write ${formatTokens(run.usage.cacheCreationTokens)}${run.usage.thinkingTokens ? ` · thinking ${formatTokens(run.usage.thinkingTokens)}` : ''}`}
        >
          {formatTokens(run.usage.outputTokens)} out
        </span>
      )}
      {run.sessionId && (
        <button
          type="button"
          className="session-id mono"
          onClick={copy}
          title={run.sessionId}
          data-testid="run-session"
        >
          session {shortId(run.sessionId)} {copied ? <Check size={11} /> : <Copy size={11} />}
        </button>
      )}
      {run.retries > 0 && (
        <span className="tag" title="API retries during this run">
          {run.retries} retries
        </span>
      )}
      {run.error && !cancelled && (
        <div className="run-error" data-testid="run-error">
          <span>{run.error.message}</span>
          {run.error.stderrTail && (
            <details>
              <summary>Error output</summary>
              <pre>{run.error.stderrTail}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
