import { shortenPath } from '@/core/summarize';
import type { Patch } from '@/core/types';

/**
 * How many diff lines one file's patch may render before it is cut off. A single Edit result can
 * carry a whole generated file; past a few hundred lines nobody is reading it in a lane column.
 */
const MAX_HUNK_LINES = 400;

export function HunkView({ patch, cwd }: { patch: Patch; cwd?: string }) {
  let shown = 0;
  return (
    <div className="hunkview" data-testid="hunk">
      <div className="hunk-file mono">{shortenPath(patch.filePath, cwd)}</div>
      {patch.hunks.map((h, hi) => {
        let oldN = h.oldStart;
        let newN = h.newStart;
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: hunks are a static, ordered diff render, never reordered/filtered.
          <div className="hunk" key={`${hi}-${h.oldStart}-${h.newStart}`}>
            <div className="hunk-header mono">
              @@ -{h.oldStart},{h.oldLines} +{h.newStart},{h.newLines} @@
            </div>
            {h.lines.map((line, li) => {
              if (shown >= MAX_HUNK_LINES) return null;
              shown += 1;
              const kind = line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'ctx';
              const o = kind === 'add' ? '' : String(oldN++);
              const n = kind === 'del' ? '' : String(newN++);
              return (
                // biome-ignore lint/suspicious/noArrayIndexKey: diff lines are a static, ordered render, never reordered/filtered.
                <div className={`hunk-line hunk-${kind}`} key={li}>
                  <span className="hunk-no">{o}</span>
                  <span className="hunk-no">{n}</span>
                  <span className="hunk-sign">{line[0]}</span>
                  <span className="hunk-text">{line.slice(1)}</span>
                </div>
              );
            })}
          </div>
        );
      })}
      {shown >= MAX_HUNK_LINES && (
        <div className="faint hunk-more">diff truncated at {MAX_HUNK_LINES} lines</div>
      )}
    </div>
  );
}
