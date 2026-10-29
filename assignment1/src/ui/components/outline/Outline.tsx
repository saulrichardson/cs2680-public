'use client';
import { toolDisplayName } from '@/core/classify';
import type { CallRef } from '@/core/ref';
import { sameCall } from '@/core/ref';
import { TERMINAL_STATUSES } from '@/core/status';
import type { OutlineItem, RunStatus } from '@/core/types';
import { StatusGlyph } from '@/ui/components/blocks/StatusGlyph';
import { ToolChip } from '@/ui/components/blocks/ToolChip';

/** One heading's worth of outline rows: a run's calls, or one delegate's. */
export interface OutlineGroup {
  /** Stable across renders; also what React keys the group by. */
  key: string;
  /** The run these calls belong to — half of every `CallRef` the rows produce. */
  runId: string;
  /** Shown above the rows in the sidebar variant only; the rail has no room for it. */
  label: string;
  /** Whether the calls can still change, which is what the pending glyphs mean. */
  status: RunStatus;
  items: OutlineItem[];
}

/**
 * The tool outline beside a trajectory.
 *
 * Deliberately told what to list rather than working it out: the same rail has to serve the parent
 * conversation (every run in the session) and a single delegate's own calls, and those two differ
 * only in which calls they contain and where a click scrolls to. Keeping the derivation with the
 * thing that owns the scrolling — `LaneOutline` below for the lane's virtualized list, `AgentPanel`
 * for the child panel's own scroller — is what stops a click landing in the wrong view.
 */
export function Outline({
  groups,
  variant,
  active,
  selected,
  onJump,
}: {
  groups: OutlineGroup[];
  variant: 'rail' | 'sidebar';
  active: CallRef | null;
  selected: CallRef | null;
  onJump(ref: CallRef): void;
}) {
  return (
    <nav
      className={`outline outline-${variant}`}
      data-testid="outline"
      data-variant={variant}
      aria-label="Trajectory outline"
    >
      {groups.map((group) => (
        <div className="outline-group" key={group.key}>
          {variant === 'sidebar' && (
            <div className="outline-prompt truncate" title={group.label}>
              {group.label || '…'}
            </div>
          )}
          {group.items.map((item) => {
            const ref = { runId: group.runId, callId: item.callId };
            return (
              <button
                type="button"
                key={`${group.key}:${item.callId}`}
                className={`outline-item${sameCall(active, ref) ? ' active' : ''}${sameCall(selected, ref) ? ' selected' : ''}`}
                style={{ paddingLeft: 6 + item.depth * (variant === 'rail' ? 6 : 14) }}
                onClick={() => onJump(ref)}
                title={`${item.name} · ${item.summary}`}
                data-testid="outline-item"
                data-depth={item.depth}
              >
                <ToolChip name={item.name} toolClass={item.toolClass} compact />
                {variant === 'sidebar' && (
                  <>
                    <span className="outline-name">{toolDisplayName(item.name)}</span>
                    <span className="outline-summary truncate mono">{item.summary}</span>
                    <StatusGlyph status={item.status} runEnded={TERMINAL_STATUSES.has(group.status)} />
                  </>
                )}
                {variant === 'rail' && <span className={`outline-dot status-${item.status}`} />}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
