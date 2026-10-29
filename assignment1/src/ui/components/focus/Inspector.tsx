'use client';
import { X } from 'lucide-react';
import { getCall } from '@/core/reducer';
import { summarizeInput } from '@/core/summarize';
import type { RunView, ToolCall } from '@/core/types';
import { StatusGlyph } from '@/ui/components/blocks/StatusGlyph';
import { ToolChip } from '@/ui/components/blocks/ToolChip';
import { HunkView } from '@/ui/components/diff/HunkView';
import { formatClock, formatDuration, formatTokens } from '@/ui/format';
import { useLaneRuns, useMissionStore } from '@/ui/store/missionStore';
import type { CallRef } from '@/ui/store/rows';

/**
 * The selected call, in the run it belongs to. Resolving by call id alone returned the first run in
 * the lane that happened to hold that id, so replaying a recording into its own lane pointed the
 * inspector at the original (readiness review R7).
 */
function findCall(runs: RunView[], ref: CallRef): { call: ToolCall; run: RunView } | null {
  const run = runs.find((r) => r.runId === ref.runId);
  const call = run ? getCall(run, ref.callId) : undefined;
  return run && call ? { call, run } : null;
}

/** Small scalar fields from `tool_use_result` worth surfacing; excludes large payloads like `prompt`. */
const DETAIL_ALLOWLIST = new Set([
  'status',
  'agentType',
  'resolvedModel',
  'totalTokens',
  'totalToolUseCount',
  'totalDurationMs',
  'interrupted',
  'noOutputExpected',
  'isImage',
  'type',
  'filePath',
  'commandName',
  'success',
  'userModified',
  'replaceAll',
]);

const DETAIL_MAX_LENGTH = 200;

function formatDetailValue(v: string | number | boolean): { text: string; title?: string } {
  if (typeof v === 'string' && v.length > DETAIL_MAX_LENGTH) {
    return { text: `${v.slice(0, DETAIL_MAX_LENGTH)}…`, title: v };
  }
  return { text: String(v) };
}

export function Inspector({ laneId, call: ref }: { laneId: string; call: CallRef }) {
  const runs = useLaneRuns(laneId);
  const select = useMissionStore((s) => s.select);
  const found = findCall(runs, ref);
  if (!found) return null;
  const { call, run } = found;
  const { primary } = summarizeInput(call.name, call.input, run.cwd);
  // A delegate's real output is the task's report, not its tool result (see ToolCallCard).
  const resultText = call.task?.summary ?? call.result?.text;
  const raw =
    call.result && call.result.text !== resultText && call.result.text.length > 0
      ? call.result.text
      : undefined;
  const structured = call.result?.structured as Record<string, unknown> | undefined;
  const meta = structured
    ? (Object.entries(structured).filter(
        ([k, v]) =>
          DETAIL_ALLOWLIST.has(k) &&
          (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'),
      ) as [string, string | number | boolean][])
    : [];
  return (
    <aside className="inspector" data-testid="inspector" aria-label="Call inspector">
      <header className="inspector-head">
        <ToolChip name={call.name} toolClass={call.toolClass} />
        <span className="inspector-tool" data-testid="inspector-tool">
          {call.name}
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => select(laneId, null)}
          aria-label="Close inspector"
          data-testid="inspector-close"
        >
          <X size={14} />
        </button>
      </header>
      <div className="inspector-body">
        <section>
          <h3>Status</h3>
          <p className="inspector-status" data-testid="inspector-status">
            <StatusGlyph status={call.status} runEnded={run.status !== 'running'} /> {call.status}
            {call.result?.isError ? ' (tool reported an error)' : ''}
          </p>
          <dl className="kv">
            <dt>Started</dt>
            <dd className="mono">{formatClock(call.ts)}</dd>
            <dt>Duration</dt>
            <dd className="mono">{formatDuration(call.durationMs)}</dd>
            {call.parentToolUseId && (
              <>
                <dt>Parent</dt>
                <dd className="mono">{call.parentToolUseId}</dd>
              </>
            )}
            {call.task?.subagentType && (
              <>
                <dt>Subagent</dt>
                <dd>
                  {call.task.subagentType}
                  {call.task.totalTokens ? ` · ${formatTokens(call.task.totalTokens)} tokens` : ''}
                </dd>
              </>
            )}
          </dl>
        </section>
        <section>
          <h3>Input</h3>
          <div className="inspector-caption mono faint truncate" title={primary}>
            {primary}
          </div>
          <pre className="inspector-pre" data-testid="inspector-input">
            {JSON.stringify(call.input, null, 2)}
          </pre>
        </section>
        {call.patches.length > 0 && (
          <section>
            <h3>Changes</h3>
            {call.patches.map((p) => (
              <HunkView key={p.filePath} patch={p} cwd={run.cwd} />
            ))}
          </section>
        )}
        {(call.task?.summary || call.result) && (
          <section>
            <h3>Result</h3>
            <pre className="inspector-pre" data-testid="inspector-result">
              {resultText || <span className="faint">(empty)</span>}
            </pre>
            {/* What the Agent tool itself returned when the report above came from the task: for an
                async launch that is the harness's own "Async agent launched successfully" note,
                which is worth keeping but not worth reading first. */}
            {raw !== undefined && (
              <details className="inspector-raw">
                <summary>Raw result</summary>
                <pre className="inspector-pre" data-testid="inspector-raw-result">
                  {raw}
                </pre>
              </details>
            )}
          </section>
        )}
        {meta.length > 0 && (
          <section>
            <h3>Details</h3>
            <dl className="kv">
              {meta.map(([k, v]) => {
                const { text, title } = formatDetailValue(v);
                return (
                  <div key={k} className="kv-row">
                    <dt>{k}</dt>
                    <dd className="mono" title={title}>
                      {text}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </section>
        )}
      </div>
    </aside>
  );
}
