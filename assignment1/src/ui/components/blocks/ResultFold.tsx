'use client';
import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { Patch } from '@/core/types';
import { HunkView } from '@/ui/components/diff/HunkView';
import { TextBlock } from './TextBlock';

/** Lines of a tool result shown before the "Show N more lines" toggle takes over. */
const FOLD_AT = 12;

export function ResultFold({
  text,
  isError,
  patches,
  cwd,
  markdown = false,
}: {
  text: string;
  isError: boolean;
  patches: Patch[];
  cwd?: string;
  /** A subagent's report is written for a person to read, so it renders as markdown, not as the
   * fixed-width tool output every other result is. Folding still counts source lines. */
  markdown?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const lines = text.length ? text.split('\n') : [];
  const hidden = Math.max(0, lines.length - FOLD_AT);
  const visible = open ? lines : lines.slice(0, FOLD_AT);
  const hasText =
    lines.length > 0 &&
    !(patches.length > 0 && /has been updated successfully|File created successfully/.test(text));
  return (
    <div className={`result${isError ? ' result-error' : ''}`} data-testid="tool-result">
      {patches.map((p) => (
        <HunkView key={p.filePath} patch={p} cwd={cwd} />
      ))}
      {hasText && (
        <>
          {markdown ? (
            <div className="result-report">
              <TextBlock markdown={visible.join('\n')} />
            </div>
          ) : (
            <pre className="result-text">{visible.join('\n')}</pre>
          )}
          {hidden > 0 && (
            <button
              type="button"
              className="fold-toggle"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              data-testid="result-fold-toggle"
            >
              <ChevronRight size={12} className={open ? 'rot' : ''} />{' '}
              {open ? 'Show less' : `Show ${hidden} more lines`}
            </button>
          )}
        </>
      )}
    </div>
  );
}
