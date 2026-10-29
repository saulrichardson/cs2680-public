'use client';
import { useRouter } from 'next/navigation';
import { memo, useMemo } from 'react';
import { getCall } from '@/core/reducer';
import type { Block, RunView } from '@/core/types';
import { useMissionStore } from '@/ui/store/missionStore';
import { NoticeRow, UnparsedRow } from './NoticeRow';
import { TextBlock, type WorkspaceLinks } from './TextBlock';
import { ThinkingRow } from './ThinkingRow';
import { ToolCallCard } from './ToolCallCard';

function useWorkspaceLinks(run: RunView): WorkspaceLinks {
  const router = useRouter();
  return useMemo(
    () => ({
      laneId: run.laneId,
      cwd: run.cwd,
      // A trajectory's prose is written from the working directory, so a bare `notes.md` means the
      // root of it.
      dir: '',
      onOpen: (path) => {
        useMissionStore.getState().showPane(run.laneId, { kind: 'outputs', path });
        // The board shows no Outputs pane, so a link clicked there would otherwise appear to do
        // nothing. Navigating to the session is a no-op when that is already where we are.
        router.push(`/lanes/${run.laneId}`);
      },
    }),
    [run.laneId, run.cwd, router],
  );
}

export const BlockRow = memo(function BlockRow({
  block,
  run,
  depth = 0,
  siblings = run.blocks,
}: {
  block: Block;
  run: RunView;
  depth?: number;
  siblings?: Block[];
}) {
  // The agent's own prose about its own workspace: when it says `[Plan](designs/plan.svg)` that is a
  // file it just wrote, and the link has to open it rather than sending the console to a route that
  // does not exist. Resolved against the session's working directory.
  const links = useWorkspaceLinks(run);
  switch (block.kind) {
    case 'text':
      return <TextBlock markdown={block.markdown} links={links} />;
    case 'thinking': {
      const isLast = siblings.at(-1) === block;
      return <ThinkingRow live={run.status === 'running' && isLast} />;
    }
    case 'tool': {
      const call = getCall(run, block.callId);
      return call ? <ToolCallCard call={call} run={run} depth={depth} /> : null;
    }
    case 'subagent-prompt':
      return (
        <div className="subagent-prompt" data-testid="subagent-prompt">
          <span className="faint">Subagent task</span>
          <p>{block.text}</p>
        </div>
      );
    case 'notice':
      return <NoticeRow block={block} />;
    case 'unparsed':
      return <UnparsedRow block={block} />;
    default:
      return null;
  }
});
