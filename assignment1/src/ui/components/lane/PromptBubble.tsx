'use client';
import { FileDown, Play, RotateCcw } from 'lucide-react';
import type { RunView } from '@/core/types';
import { formatClock, shortId } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { RunMenu } from './RunMenu';

export function PromptBubble({ run }: { run: RunView }) {
  // `useLaneHydration` retries three times and then stops. The trajectory below is blank for such a
  // run, which otherwise reads as an agent that did nothing at all.
  const eventsFailed = useMissionStore((s) => s.eventsFailed[run.runId] ?? false);
  return (
    <div className="prompt-bubble" data-testid="prompt-bubble">
      <div className="prompt-meta">
        <span className="prompt-you">You</span>
        <span className="faint">{formatClock(run.startedAt)}</span>
        {run.resumedFrom && (
          <span className="tag" title="Continues the previous session">
            <RotateCcw size={11} /> resumed {shortId(run.sessionId)}
          </span>
        )}
        {/* What this row is, from the run's recorded provenance rather than from a guess about its
            prompt: playback changed nothing in the lane's directory, and the console says so
            wherever the row appears. */}
        {run.origin === 'replay' && (
          <span className="tag" title="Playback of a run recorded here. Nothing was executed.">
            <Play size={11} /> replay
          </span>
        )}
        {run.origin === 'import' && (
          <span className="tag" title="A recording made elsewhere. Nothing was executed in this lane.">
            <FileDown size={11} /> recording
          </span>
        )}
        <span className="spacer" />
        <RunMenu run={run} />
      </div>
      <p className="prompt-text">{run.prompt || <span className="faint">…</span>}</p>
      {eventsFailed && (
        <p className="error prompt-events-failed" role="alert" data-testid="events-failed">
          Could not load this run's events — reload the page.
        </p>
      )}
    </div>
  );
}
