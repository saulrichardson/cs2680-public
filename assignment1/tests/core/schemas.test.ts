import { describe, expect, it } from 'vitest';
import {
  contentBlocks,
  parseContentBlock,
  parseLine,
  parseTextBlock,
  parseThinkingBlock,
  parseToolResultBlock,
  parseToolUseBlock,
} from '@/core/schemas';
import { readFixtureLines } from '../helpers/fixtures';

const FIXTURES = ['flat', 'subagent', 'failed', 'flat-allowed', 'subagent-forward'] as const;

describe('parseLine', () => {
  it.each(FIXTURES)('parses every line of %s without throwing', (name) => {
    const lines = readFixtureLines(name);
    const results = lines.map(parseLine);
    expect(results.every((r) => r.ok)).toBe(true);
    for (const r of results) {
      if (!r.ok) continue;
      expect(typeof r.event.type).toBe('string');
    }
  });

  it('returns ok:false with the raw text for malformed JSON', () => {
    const r = parseLine('{"type": "assistant", "message": ');
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.raw).toContain('assistant');
      expect(r.error.length).toBeGreaterThan(0);
    }
  });

  it('rejects JSON that is not an object with a string type', () => {
    expect(parseLine('42').ok).toBe(false);
    expect(parseLine('{"notype": 1}').ok).toBe(false);
    expect(parseLine('[1,2]').ok).toBe(false);
  });

  it('keeps unknown fields (lenient parsing)', () => {
    const r = parseLine('{"type":"system","subtype":"brand_new","shiny":true}');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.event.shiny).toBe(true);
  });
});

describe('content block parsing', () => {
  it('classify blocks from a real assistant event and a real user event', () => {
    const lines = readFixtureLines('flat');
    const events = lines.map(parseLine).flatMap((r) => (r.ok ? [r.event] : []));
    const assistant = events.filter((e) => e.type === 'assistant');
    const user = events.filter((e) => e.type === 'user');
    const kinds = new Set<string>();
    for (const e of assistant)
      for (const b of contentBlocks(e)) {
        const parsed = parseContentBlock(b);
        if (!parsed) continue;
        kinds.add(parsed.kind);
        if (parsed.kind === 'tool_use') {
          expect(typeof parsed.block.id).toBe('string');
          expect(typeof parsed.block.name).toBe('string');
        }
      }
    for (const e of user)
      for (const b of contentBlocks(e)) {
        const parsed = parseContentBlock(b);
        if (parsed?.kind === 'tool_result') {
          kinds.add('tool_result');
          expect(typeof parsed.block.tool_use_id).toBe('string');
        }
      }
    expect([...kinds].sort()).toEqual(['text', 'thinking', 'tool_result', 'tool_use']);
  });

  it('contentBlocks handles a string user message', () => {
    expect(contentBlocks({ type: 'user', message: { role: 'user', content: 'hello' } })).toEqual([
      { type: 'text', text: 'hello' },
    ]);
  });

  /**
   * R6. The schema gives `input` a default, but the old `isToolUseBlock` guard only asked whether
   * the block *would* parse and then handed the caller the original, undefaulted object — so a
   * `tool_use` block with no `input` at all reached the reducer as `{ input: undefined }` and the
   * first thing that read `call.input.file_path` threw. A parse function returns the normalized
   * value, so the default the schema promises is the value callers actually get.
   */
  it('returns the schema default for a tool_use block with no input', () => {
    const parsed = parseToolUseBlock({ type: 'tool_use', id: 'missing-input', name: 'Read' });
    expect(parsed).toEqual({ type: 'tool_use', id: 'missing-input', name: 'Read', input: {} });
    // a real input is preserved untouched, unknown keys included
    expect(
      parseToolUseBlock({ type: 'tool_use', id: 'x', name: 'Read', input: { file_path: '/a' } }),
    ).toMatchObject({ input: { file_path: '/a' } });
  });

  it('rejects malformed blocks instead of narrowing them', () => {
    // a tool_use with no id/name is not a tool call, whatever it claims to be
    expect(parseToolUseBlock({ type: 'tool_use', name: 'Read' })).toBeNull();
    expect(parseToolUseBlock({ type: 'tool_use', id: 7, name: 'Read' })).toBeNull();
    // `input` has a default, but a non-object input is a contradiction, not a default
    expect(parseToolUseBlock({ type: 'tool_use', id: 'a', name: 'Read', input: 'oops' })).toBeNull();
    expect(parseTextBlock({ type: 'text' })).toBeNull();
    expect(parseTextBlock({ type: 'text', text: 42 })).toBeNull();
    expect(parseToolResultBlock({ type: 'tool_result' })).toBeNull();
  });

  /**
   * Two different situations that used to answer the same way. A block type this console has no
   * rendering for is nothing to worry about; a `tool_use` that is not a tool use is a tool call the
   * trajectory cannot show, and silently dropping it makes the run look like one where the agent
   * never made that call.
   */
  it('tells an unsupported block type apart from a malformed known one', () => {
    // unsupported: nothing wrong with it, nothing to show
    expect(parseContentBlock({ type: 'image', source: {} })).toBeNull();
    expect(parseContentBlock({ type: 'server_tool_use_from_the_future', id: 'x' })).toBeNull();

    // malformed: it says what it is and then is not that
    expect(parseContentBlock({ type: 'tool_use', name: 'Read' })).toMatchObject({
      kind: 'malformed',
      type: 'tool_use',
    });
    expect(parseContentBlock({ type: 'text', text: 42 })).toMatchObject({
      kind: 'malformed',
      type: 'text',
    });
    expect(parseContentBlock({ type: 'tool_result' })).toMatchObject({
      kind: 'malformed',
      type: 'tool_result',
    });
    const bad = parseContentBlock({ type: 'tool_use', name: 'Read' });
    expect(bad?.kind === 'malformed' && bad.reason).toMatch(/id/);
  });

  it('keeps optional content-block fields absent rather than inventing them', () => {
    // `thinking` and `is_error` have no defaults: a block without them must not gain one
    expect(parseThinkingBlock({ type: 'thinking' })).toEqual({ type: 'thinking' });
    expect(parseToolResultBlock({ type: 'tool_result', tool_use_id: 't1' })).toEqual({
      type: 'tool_result',
      tool_use_id: 't1',
    });
  });
});
