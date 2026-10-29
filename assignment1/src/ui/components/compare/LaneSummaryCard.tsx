'use client';
import { Check, GitBranch } from 'lucide-react';
import Link from 'next/link';
import type { CompareLane } from '@/core/types';
import { formatCount, formatDurationCompact, formatUsd } from '@/ui/format';
import { useLaneStatus, useMissionStore } from '@/ui/store/missionStore';
import { useToasts } from '@/ui/store/toasts';
import { laneHueStyle } from '@/ui/theme/laneHue';
import { trpc } from '@/ui/trpc/client';

export function LaneSummaryCard({
  lane,
  groupId,
  kept,
  superseded,
  best,
  onChanged,
}: {
  lane: CompareLane;
  groupId: string;
  kept: boolean;
  /**
   * This lane's kept run is no longer its latest execution, so the worktree may no longer hold only
   * the work that was chosen. Deliberately "may": a later execution that only read files changed
   * nothing, and this console does not claim to know which it was.
   */
  superseded: boolean;
  /** The winning figures as text, or null where the lanes drew (see CompareStrip). */
  best: { cost: string | null; wall: string | null };
  onChanged: () => void;
}) {
  // Only status/activity come from the live store here — cost, wall time, and everything `best` is
  // compared against are already merged onto `lane` by CompareStrip, so this card and the strip's
  // best-of marker always agree on which numbers they're looking at.
  const live = useMissionStore((s) => (lane.runId ? s.runs[lane.runId] : undefined));
  // Any operation in the lane, a playback included — not just the latest execution. A replay is
  // what the lane is doing right now while it plays, and the server refuses a Keep during one.
  const { running: laneBusy } = useLaneStatus(lane.laneId);
  const keep = trpc.fanout.keep.useMutation({
    onSuccess: onChanged,
    onError: (e) =>
      useToasts.getState().push({ kind: 'error', title: 'Could not keep this lane', text: e.message }),
  });
  const status = live?.status ?? lane.status;
  const numbers = lane.numbers;
  const summary = lane.summary;
  const wall = lane.wallMs;
  const attempts = lane.attempts.length;
  const gaps = lane.metricGaps;
  // A figure some attempt never reported is a floor, not a total. Marked "at least" rather than
  // quietly understated, and never eligible for the cheapest/fastest marker.
  //
  // Only a *known* subtotal is marked: `≥ –` claims a lower bound on a figure nobody reported, and
  // nothing is known about it at all. Unknown stays unknown.
  const atLeast = (value: number | undefined, text: string, missing: number) =>
    missing > 0 && value !== undefined ? `≥ ${text}` : text;
  const costText = atLeast(numbers?.costUsd, formatUsd(numbers?.costUsd), gaps.costUsd);
  const wallIncomplete = attempts > 0 && lane.attempts.some((a) => a.endedAt === undefined);
  const wallText = atLeast(wall, formatDurationCompact(wall), wallIncomplete ? 1 : 0);
  const added = lane.files.reduce((n, f) => n + f.added, 0);
  const removed = lane.files.reduce((n, f) => n + f.removed, 0);
  return (
    <article
      className={`lane-summary${kept ? ' kept' : ''}`}
      style={laneHueStyle(lane.groupIndex)}
      data-testid="lane-summary"
    >
      <header>
        <Link href={`/lanes/${lane.laneId}`} className="lane-summary-name">
          {lane.name}
        </Link>
        <span
          className={`pill pill-${status === 'none' ? 'cancelled' : status}`}
          data-testid="summary-status"
        >
          {status === 'none' ? 'no run' : status}
        </span>
        {kept && (
          <span className="pill pill-finished" data-testid="kept-badge">
            <Check size={11} /> kept
          </span>
        )}
        {/* The badge is never moved to work the operator did not choose: it says so instead. */}
        {kept && superseded && (
          <span
            className="pill pill-cancelled"
            title="This lane has executed again since, so the worktree may no longer hold only the work you kept. Worth reviewing."
            data-testid="kept-superseded"
          >
            superseded
          </span>
        )}
      </header>
      {/* The diff below spans every execution in this lane, so the figures do too. Saying which
          attempts they cover is what keeps "$0.40" from reading as the cost of the whole result. */}
      {attempts > 1 && (
        <p className="faint compare-attempts" data-testid="summary-attempts">
          totals across {formatCount(attempts, 'attempt')}
        </p>
      )}
      {/* Deliberately not a count: an attempt may be missing its cost and not its turns, so no one
          number describes how many attempts are incomplete — and "reported no figures" was wrong
          for the common case of a single missing field. A marked figure is a known subtotal; an
          unmarked "–" is a figure nobody reported, which is never read as zero. */}
      {(gaps.costUsd > 0 || gaps.numTurns > 0 || wallIncomplete) && (
        <p className="faint compare-attempts" data-testid="summary-incomplete">
          Some figures are missing; marked values are known subtotals
        </p>
      )}
      <dl className="stats">
        <div className={best.cost !== null && costText === best.cost ? 'best' : ''}>
          <dt>{attempts > 1 ? 'Total cost' : 'Cost'}</dt>
          <dd className="mono" data-testid="summary-cost">
            {costText}
          </dd>
        </div>
        <div className={best.wall !== null && wallText === best.wall ? 'best' : ''}>
          <dt>{attempts > 1 ? 'Total wall' : 'Wall'}</dt>
          <dd className="mono">{wallText}</dd>
        </div>
        <div>
          <dt>Turns</dt>
          <dd className="mono">
            {numbers?.numTurns === undefined
              ? '–'
              : atLeast(numbers.numTurns, String(numbers.numTurns), gaps.numTurns)}
          </dd>
        </div>
        <div>
          <dt>Calls</dt>
          <dd className="mono" data-testid="summary-calls">
            {summary ? `${summary.callCount}` : '–'}
            {summary && summary.errorCount > 0 ? (
              <span className="error"> · {formatCount(summary.errorCount, 'error')}</span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Files</dt>
          <dd className="mono">
            {lane.files.length} <span className="diff-add">+{added}</span>{' '}
            <span className="diff-del">−{removed}</span>
          </dd>
        </div>
        {summary && (
          <div>
            <dt>Subagents</dt>
            <dd className="mono">{summary.subagentCount}</dd>
          </div>
        )}
      </dl>
      {summary && (
        <div className="class-bar" title="Tool calls by class">
          {(['search', 'mutate', 'execute', 'delegate', 'network', 'other'] as const).map(
            (c) =>
              summary.callsByClass[c] > 0 && (
                <span
                  key={c}
                  className={`class-seg cls-${c}`}
                  style={{ flex: summary.callsByClass[c] }}
                  title={`${c}: ${summary.callsByClass[c]}`}
                />
              ),
          )}
        </div>
      )}
      <footer>
        <span className="faint mono truncate" title={lane.worktreePath}>
          <GitBranch size={11} /> <span data-testid="kept-branch">{lane.branch || '–'}</span>
        </span>
        <span className="spacer" />
        {/* Keeping is a judgement about finished work, and the server enforces that: the lane's
            latest execution must be terminal and the lane idle. The button says so up front rather
            than offering a choice that comes back as a precondition error. Clearing an existing
            choice stays available — it takes nothing back that the lane is in the middle of. */}
        <button
          type="button"
          className={`btn${kept ? ' btn-kept' : ''}`}
          disabled={!lane.runId || keep.isPending || (!kept && laneBusy)}
          title={
            !kept && laneBusy ? 'This lane is still running. Keep a result once it has finished.' : undefined
          }
          onClick={() => keep.mutate({ groupId, runId: kept ? null : lane.runId })}
          data-testid="keep"
        >
          {kept ? (
            <>
              <Check size={12} /> Kept
            </>
          ) : (
            'Keep'
          )}
        </button>
      </footer>
      {lane.error && <p className="error">{lane.error}</p>}
    </article>
  );
}
