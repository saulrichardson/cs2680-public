'use client';
import { memo } from 'react';
import { TERMINAL_STATUSES } from '@/core/status';
import { summarizeInput } from '@/core/summarize';
import type { RunView, ToolCall } from '@/core/types';
import { formatDuration, formatTokens } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { callElementId, sameCall } from '@/ui/store/rows';
import { ResultFold } from './ResultFold';
import { StatusGlyph } from './StatusGlyph';
import { SubagentGroup } from './SubagentGroup';
import { ToolChip } from './ToolChip';

/**
 * What a delegate card shows in its meta slot. An Agent call in CLI 2.1.270 is answered within
 * milliseconds ("Async agent launched successfully…") while the subagent keeps working for a
 * minute, so the card follows the *task*: its live activity while it runs, its own numbers once it
 * has reported. The tool call's own duration would be the launch latency, which tells nobody
 * anything.
 */
function delegateNumbers(task: ToolCall['task']): string | undefined {
  const parts = [
    task?.durationMs !== undefined ? formatDuration(task.durationMs) : undefined,
    task?.toolUses !== undefined ? `${task.toolUses} tool calls` : undefined,
    task?.totalTokens !== undefined ? `${formatTokens(task.totalTokens)} tokens` : undefined,
  ].filter((p): p is string => p !== undefined);
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

export const ToolCallCard = memo(function ToolCallCard({
  call,
  run,
  depth,
}: {
  call: ToolCall;
  run: RunView;
  depth: number;
}) {
  const { primary, secondary } = summarizeInput(call.name, call.input, run.cwd);
  const select = useMissionStore((s) => s.select);
  // Both halves: a lane can hold the same CLI call id more than once (a replay of a run it already
  // has, the same recording imported twice), and only the pair says which card this is.
  const ref = { runId: run.runId, callId: call.id };
  const selected = useMissionStore((s) => sameCall(s.selection.call, ref));
  const runEnded = TERMINAL_STATUSES.has(run.status);
  const command =
    call.name === 'Bash' && typeof call.input.command === 'string' && call.input.command.includes('\n')
      ? call.input.command
      : null;
  const delegate = call.toolClass === 'delegate';
  const task = call.task;
  const pending = call.status === 'pending';
  const activity = delegate && pending && task?.activity ? `Running ${task.activity}…` : undefined;
  const numbers = delegate && !pending ? delegateNumbers(task) : undefined;
  // The subagent's real report is the task summary; the tool result for an async launch is internal
  // bookkeeping and is never shown here (the inspector keeps it under a fold).
  const shown =
    delegate && task?.summary
      ? { text: task.summary, isError: false, markdown: true }
      : call.result && !call.result.internal
        ? { text: call.result.text, isError: call.result.isError, markdown: false }
        : null;
  const children =
    call.children.length > 0 ? (
      <SubagentGroup blocks={call.children} run={run} depth={depth + 1} call={call} />
    ) : null;
  const result = shown ? (
    <ResultFold
      text={shown.text}
      markdown={shown.markdown}
      isError={shown.isError}
      patches={call.patches}
      cwd={run.cwd}
    />
  ) : null;
  return (
    <article
      className={`tool-card cls-${call.toolClass}${selected ? ' selected' : ''}`}
      data-testid="tool-card"
      data-call-id={call.id}
      data-run-id={run.runId}
      data-tool={call.name}
      data-status={call.status}
      data-depth={depth}
      id={callElementId(ref)}
    >
      {/* biome-ignore lint/a11y/useSemanticElements: header must stay a header for card layout; role="button" makes it keyboard/AT operable. */}
      <header
        className="tool-head"
        onClick={() => select(run.laneId, ref)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          // Space is half of what a button answers to; preventDefault stops it scrolling the lane.
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          select(run.laneId, ref);
        }}
      >
        <ToolChip name={call.name} toolClass={call.toolClass} />
        <span className="tool-primary mono truncate" title={delegate ? undefined : primary}>
          {delegate ? (task?.description ?? primary) : primary}
        </span>
        {activity ? (
          <span className="tool-secondary truncate" title={activity}>
            {activity}
          </span>
        ) : (
          secondary && (
            <span className="tool-secondary tool-desc truncate" title={secondary}>
              {secondary}
            </span>
          )
        )}
        <span className="tool-meta">
          {numbers ? (
            <span className="faint mono truncate">{numbers}</span>
          ) : delegate ? null : call.durationMs !== undefined ? (
            <span className="faint mono">{formatDuration(call.durationMs)}</span>
          ) : call.elapsedSeconds ? (
            <span className="faint mono">{call.elapsedSeconds}s</span>
          ) : null}
          <StatusGlyph status={call.status} runEnded={runEnded} />
        </span>
      </header>
      {command && <pre className="tool-command mono">{command}</pre>}
      {/* A delegate reads top to bottom: what it was asked to do, what it did, then its report. */}
      {delegate ? (
        <>
          {children}
          {result}
        </>
      ) : (
        <>
          {result}
          {children}
        </>
      )}
    </article>
  );
});
