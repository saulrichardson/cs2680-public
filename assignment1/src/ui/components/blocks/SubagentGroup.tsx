'use client';
import { ChevronDown } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Block, RunView, ToolCall } from '@/core/types';
import { formatDuration, formatTokens } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { callKey } from '@/ui/store/rows';
import { BlockRow } from './BlockRow';

export function SubagentGroup({
  blocks,
  run,
  depth,
  call,
}: {
  blocks: Block[];
  run: RunView;
  depth: number;
  call?: ToolCall;
}) {
  const [open, setOpen] = useState(blocks.length <= 8);
  // An outline jump to a call nested in here has to unfold the group first, and the request outlives
  // the jump on purpose: this component is inside a virtualized row, so it usually mounts only once
  // the jump has scrolled that row into the window — after the request was made. A plain number, so
  // subscribing needs no `useShallow`.
  const openRequest = useMissionStore((s) =>
    call ? (s.groupOpenRequests[callKey({ runId: run.runId, callId: call.id })] ?? 0) : 0,
  );
  useEffect(() => {
    if (openRequest > 0) setOpen(true);
  }, [openRequest]);
  const task = call?.task;
  const status =
    task?.status ?? (call?.status === 'done' ? 'completed' : call?.status === 'error' ? 'failed' : 'running');
  return (
    <div className="subagent-wrap">
      <button
        type="button"
        className="subagent-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="subagent-toggle"
      >
        <ChevronDown size={13} className={open ? '' : 'rot-neg'} />
        <span className="subagent-summary" data-testid="subagent-summary">
          <strong>Subagent</strong>
          {task?.subagentType && <span className="tag">{task.subagentType}</span>}
          <span className="faint">{blocks.length} events</span>
          {task?.toolUses !== undefined && <span className="faint">· {task.toolUses} tool calls</span>}
          {task?.totalTokens !== undefined && (
            <span className="faint">· {formatTokens(task.totalTokens)} tokens</span>
          )}
          {task?.durationMs !== undefined && (
            <span className="faint">· {formatDuration(task.durationMs)}</span>
          )}
          <span
            className={`pill pill-${status === 'completed' ? 'finished' : status === 'running' ? 'running' : 'failed'}`}
          >
            {status}
          </span>
        </span>
      </button>
      {open && (
        <div className="subagent" data-testid="subagent-group">
          {blocks.map((b) => (
            <BlockRow
              key={b.kind === 'tool' ? b.callId : b.id}
              block={b}
              run={run}
              depth={depth}
              siblings={blocks}
            />
          ))}
        </div>
      )}
    </div>
  );
}
