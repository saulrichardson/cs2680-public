import { z } from 'zod';
import type { RawEvent } from './types';

/** Any stream-json line: an object with a string `type`. Everything else is preserved. */
const RawEventSchema = z
  .object({
    type: z.string(),
    subtype: z.string().optional(),
    uuid: z.string().optional(),
    session_id: z.string().optional(),
    parent_tool_use_id: z.string().nullable().optional(),
    timestamp: z.string().optional(),
  })
  .loose();

// The content-block shapes behind the parsers below. Internal: callers go through
// `parseContentBlock` and friends, which is the only thing the reducer needs and keeps zod out of
// its imports.
const TextBlockSchema = z.object({ type: z.literal('text'), text: z.string() }).loose();
const ThinkingBlockSchema = z
  .object({ type: z.literal('thinking'), thinking: z.string().optional() })
  .loose();
const ToolUseBlockSchema = z
  .object({
    type: z.literal('tool_use'),
    id: z.string(),
    name: z.string(),
    input: z.record(z.string(), z.unknown()).default({}),
  })
  .loose();
const ToolResultBlockSchema = z
  .object({
    type: z.literal('tool_result'),
    tool_use_id: z.string(),
    content: z.unknown().optional(),
    is_error: z.boolean().optional(),
  })
  .loose();

export type TextBlockRaw = z.infer<typeof TextBlockSchema>;
export type ThinkingBlockRaw = z.infer<typeof ThinkingBlockSchema>;
export type ToolUseBlockRaw = z.infer<typeof ToolUseBlockSchema>;
export type ToolResultBlockRaw = z.infer<typeof ToolResultBlockSchema>;

export type ParsedLine = { ok: true; event: RawEvent } | { ok: false; raw: string; error: string };

export function parseLine(line: string): ParsedLine {
  const raw = line.replace(/\r$/, '');
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (err) {
    return { ok: false, raw, error: err instanceof Error ? err.message : 'invalid JSON' };
  }
  const parsed = RawEventSchema.safeParse(json);
  if (!parsed.success) return { ok: false, raw, error: 'not a stream-json event object' };
  return { ok: true, event: parsed.data as RawEvent };
}

/** Content blocks of an assistant or user event; a string message becomes one text block. */
export function contentBlocks(event: RawEvent): Record<string, unknown>[] {
  const message = event.message as { content?: unknown } | undefined;
  const content = message?.content;
  if (typeof content === 'string') return [{ type: 'text', text: content }];
  if (Array.isArray(content))
    return content.filter((b): b is Record<string, unknown> => typeof b === 'object' && b !== null);
  return [];
}

/**
 * Content blocks are *parsed*, never merely recognised.
 *
 * These used to be type guards (`isToolUseBlock(b): b is ToolUseBlockRaw`) that ran `safeParse` and
 * then handed the caller back the original object. That is a promise the runtime value does not
 * keep: `ToolUseBlockSchema` gives `input` a default of `{}`, so the guard narrowed a block with no
 * `input` at all to a type claiming to have one, and the first read of `call.input.file_path` threw
 * on a valid-JSON imported recording (readiness review R6). Returning the parsed value — or `null`
 * — makes the schema's defaults the values callers actually hold.
 *
 * Normalization applies only to the *view*. The event's own line is stored and exported verbatim
 * (`repo.insertEvent` writes the agent's original text), so nothing here edits the record of what
 * the agent said.
 */
function parser<T>(schema: { safeParse(v: unknown): { success: true; data: T } | { success: false } }) {
  return (b: Record<string, unknown>): T | null => {
    const parsed = schema.safeParse(b);
    return parsed.success ? parsed.data : null;
  };
}

export const parseTextBlock = parser<TextBlockRaw>(TextBlockSchema);
export const parseThinkingBlock = parser<ThinkingBlockRaw>(ThinkingBlockSchema);
export const parseToolUseBlock = parser<ToolUseBlockRaw>(ToolUseBlockSchema);
export const parseToolResultBlock = parser<ToolResultBlockRaw>(ToolResultBlockSchema);

/**
 * One content block, as whichever of the four shapes it actually is — or `null` for a block this
 * console has no rendering for (an `image`, a future block type) and for one that claims a shape it
 * does not have (a `tool_use` with no `id`). Callers treat `null` as "ignored", which is what the
 * run view counts in `ignoredCount`.
 */
export type ParsedContentBlock =
  | { kind: 'text'; block: TextBlockRaw }
  | { kind: 'thinking'; block: ThinkingBlockRaw }
  | { kind: 'tool_use'; block: ToolUseBlockRaw }
  | { kind: 'tool_result'; block: ToolResultBlockRaw }
  /**
   * A block that says what it is and then is not that. The caller shows it rather than dropping
   * it: a `tool_use` with no `id` is a tool call this console cannot render, and a trajectory that
   * quietly omits it is a worse lie than one that says a block could not be read.
   */
  | { kind: 'malformed'; type: string; reason: string };

/**
 * One content block, as whichever of the four shapes it actually is.
 *
 * `null` means *unsupported*: a block type this console has no rendering for — an `image`, a type
 * a future CLI introduces. There is nothing wrong with it and nothing to show, so it is counted as
 * ignored. A block of a *known* type that fails its schema is a different thing entirely, and comes
 * back as `malformed` so the trajectory can say so.
 */
export function parseContentBlock(b: Record<string, unknown>): ParsedContentBlock | null {
  // Dispatch on the discriminant first, so a malformed block of a known type is reported as
  // malformed rather than silently falling through to the next shape's parser.
  switch (b.type) {
    case 'text': {
      const block = parseTextBlock(b);
      return block ? { kind: 'text', block } : malformed('text', 'expected a string `text`');
    }
    case 'thinking': {
      const block = parseThinkingBlock(b);
      return block ? { kind: 'thinking', block } : malformed('thinking', 'expected a thinking block');
    }
    case 'tool_use': {
      const block = parseToolUseBlock(b);
      return block
        ? { kind: 'tool_use', block }
        : malformed('tool_use', 'expected a string `id` and `name`, and an object `input`');
    }
    case 'tool_result': {
      const block = parseToolResultBlock(b);
      return block
        ? { kind: 'tool_result', block }
        : malformed('tool_result', 'expected a string `tool_use_id`');
    }
    default:
      return null;
  }
}

function malformed(type: string, reason: string): ParsedContentBlock {
  return { kind: 'malformed', type, reason };
}

/** Flatten a tool_result `content` (string | block[]) to display text. Images become a placeholder. */
export function toolResultText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => {
        if (typeof b === 'string') return b;
        if (b && typeof b === 'object') {
          const rec = b as Record<string, unknown>;
          if (rec.type === 'text' && typeof rec.text === 'string') return rec.text;
          if (rec.type === 'image') return '[image]';
        }
        return '';
      })
      .filter((s) => s.length > 0)
      .join('\n');
  }
  if (content === undefined || content === null) return '';
  return JSON.stringify(content);
}
