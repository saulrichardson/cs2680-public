'use client';
import { AlertTriangle, Info, OctagonX } from 'lucide-react';
import type { NoticeBlock, UnparsedBlock } from '@/core/types';

export function NoticeRow({ block }: { block: NoticeBlock }) {
  // A stop the operator asked for is not news: one quiet line, no disclosure triangle to open and
  // no icon competing with the run footer's own Square. (The CLI's interrupt diagnostic still
  // exists — it is in the raw event stream and the inspector, not in the trajectory as an error.)
  if (block.variant === 'interrupted')
    return (
      <p className="notice-interrupt muted" data-testid="notice" data-variant="interrupted">
        {block.title}
      </p>
    );
  const Icon = block.level === 'error' ? OctagonX : block.level === 'warning' ? AlertTriangle : Info;
  return (
    <details className={`notice notice-${block.level}`} data-testid="notice">
      <summary>
        <Icon size={13} /> <span>{block.title}</span>
      </summary>
      {block.text && <pre className="notice-text">{block.text}</pre>}
    </details>
  );
}

export function UnparsedRow({ block }: { block: UnparsedBlock }) {
  return (
    <details className="notice notice-warning" data-testid="unparsed">
      <summary>
        <AlertTriangle size={13} /> <span>The agent printed a line we could not read ({block.error})</span>
      </summary>
      <pre className="notice-text">{block.raw}</pre>
    </details>
  );
}
