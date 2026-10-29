import { describe, expect, it } from 'vitest';
import { countPatchLines, extractPatches } from '@/core/patches';
import { contentBlocks, parseToolResultBlock, parseToolUseBlock } from '@/core/schemas';
import { loadFixture } from '../helpers/fixtures';

const hunk = { oldStart: 1, oldLines: 2, newStart: 1, newLines: 3, lines: [' a', '-b', '+b2', '+c'] };

describe('extractPatches', () => {
  it('reads Edit structuredPatch', () => {
    const p = extractPatches({
      filePath: '/r/a.py',
      structuredPatch: [hunk],
      oldString: 'b',
      newString: 'b2\nc',
    });
    expect(p).toEqual([{ filePath: '/r/a.py', hunks: [hunk] }]);
  });
  it('synthesizes an all-added hunk for Write create', () => {
    const p = extractPatches({
      type: 'create',
      filePath: '/r/new.md',
      content: 'x\ny',
      structuredPatch: [],
      originalFile: null,
    });
    expect(p).toEqual([
      {
        filePath: '/r/new.md',
        hunks: [{ oldStart: 0, oldLines: 0, newStart: 1, newLines: 2, lines: ['+x', '+y'] }],
      },
    ]);
  });
  it('reads Bash bashEditDiff files', () => {
    const p = extractPatches({
      stdout: '',
      bashEditDiff: { files: [{ filePath: '/r/a.py', hunks: [hunk] }], changedFiles: ['/r/a.py'] },
    });
    expect(p).toEqual([{ filePath: '/r/a.py', hunks: [hunk] }]);
  });
  it('returns [] for anything else', () => {
    expect(extractPatches({ type: 'text', file: {} })).toEqual([]);
    expect(extractPatches(undefined)).toEqual([]);
    expect(extractPatches('string')).toEqual([]);
  });
  it('finds at least one patch on every Edit in flat-allowed', () => {
    const envs = loadFixture('flat-allowed');
    const names = new Map<string, string>();
    let edits = 0;
    for (const { event } of envs) {
      if (event.type === 'assistant')
        for (const raw of contentBlocks(event)) {
          const b = parseToolUseBlock(raw);
          if (b) names.set(b.id, b.name);
        }
      if (event.type === 'user')
        for (const raw of contentBlocks(event)) {
          const b = parseToolResultBlock(raw);
          if (b && names.get(b.tool_use_id) === 'Edit') {
            edits += 1;
            expect(extractPatches(event.tool_use_result).length).toBeGreaterThanOrEqual(1);
          }
        }
    }
    expect(edits).toBe(2);
  });
});

describe('countPatchLines', () => {
  it('counts + and - lines', () => {
    expect(countPatchLines([{ filePath: 'a', hunks: [hunk] }])).toEqual({ added: 2, removed: 1 });
  });
});
