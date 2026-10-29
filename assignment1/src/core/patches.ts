import type { Hunk, Patch } from './types';

function isHunk(v: unknown): v is Hunk {
  if (!v || typeof v !== 'object') return false;
  const h = v as Record<string, unknown>;
  return (
    typeof h.oldStart === 'number' &&
    typeof h.oldLines === 'number' &&
    typeof h.newStart === 'number' &&
    typeof h.newLines === 'number' &&
    Array.isArray(h.lines) &&
    h.lines.every((l) => typeof l === 'string')
  );
}

function hunks(v: unknown): Hunk[] {
  return Array.isArray(v) ? v.filter(isHunk) : [];
}

/**
 * Every patch a tool result carries, whatever tool produced it: `Edit`/`Write` hand back a
 * `structuredPatch` for one `filePath`, `Bash` a `bashEditDiff` covering several files. The shapes
 * are distinct enough to read the result on its own, so the tool's name is not needed.
 */
export function extractPatches(structured: unknown): Patch[] {
  if (!structured || typeof structured !== 'object') return [];
  const s = structured as Record<string, unknown>;

  const bashDiff = s.bashEditDiff as { files?: unknown } | undefined;
  if (bashDiff && Array.isArray(bashDiff.files)) {
    return bashDiff.files.flatMap((f) => {
      const file = f as Record<string, unknown>;
      if (typeof file.filePath !== 'string') return [];
      const hs = hunks(file.hunks);
      return hs.length ? [{ filePath: file.filePath, hunks: hs }] : [];
    });
  }

  if (typeof s.filePath === 'string') {
    const hs = hunks(s.structuredPatch);
    if (hs.length) return [{ filePath: s.filePath, hunks: hs }];
    if (s.type === 'create' && typeof s.content === 'string' && s.content.length > 0) {
      const lines = s.content.split('\n');
      return [
        {
          filePath: s.filePath,
          hunks: [
            {
              oldStart: 0,
              oldLines: 0,
              newStart: 1,
              newLines: lines.length,
              lines: lines.map((l) => `+${l}`),
            },
          ],
        },
      ];
    }
  }
  return [];
}

export function countPatchLines(patches: Patch[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const p of patches)
    for (const h of p.hunks)
      for (const l of h.lines) {
        if (l.startsWith('+')) added += 1;
        else if (l.startsWith('-')) removed += 1;
      }
  return { added, removed };
}
