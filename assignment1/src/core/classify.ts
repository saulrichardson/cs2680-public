import { CHROME_TOOL_PREFIX, type ToolClass } from './types';

const CLASSES: Record<string, ToolClass> = {
  Read: 'search',
  Glob: 'search',
  Grep: 'search',
  LS: 'search',
  Edit: 'mutate',
  MultiEdit: 'mutate',
  Write: 'mutate',
  NotebookEdit: 'mutate',
  Bash: 'execute',
  Agent: 'delegate',
  Task: 'delegate',
  WebFetch: 'network',
  WebSearch: 'network',
};

export function classifyTool(name: string): ToolClass {
  // The Chrome extension arrives as an MCP server, so its tools are named
  // `mcp__claude-in-chrome__navigate` and so on — a family, not a fixed list, and one the CLI can
  // extend without this file knowing. They reach the outside world, which is what `network` means.
  if (name.startsWith(CHROME_TOOL_PREFIX)) return 'network';
  return CLASSES[name] ?? 'other';
}

/** Did this call drive the browser? Asked of the name, because the class is shared with WebFetch. */
export function isBrowserTool(name: string): boolean {
  return name.startsWith(CHROME_TOOL_PREFIX);
}

/**
 * The name to *show* for a tool. The exact name is kept wherever the detail belongs.
 *
 * An MCP tool's wire name is `mcp__<server>__<tool>`, so a browser call renders as
 * `mcp__claude-in-chrome__navigate`: 28 characters of prefix before the only part that says what
 * happened. In a tool chip and an outline row that prefix is the whole visible width, and a
 * trajectory of browser work became a column of identical labels. Every caller that shows this also
 * carries the full name in a `title`, and the inspector shows it verbatim.
 */
export function toolDisplayName(name: string): string {
  const match = /^mcp__[^_]+(?:[^_]|_(?!_))*__(.+)$/.exec(name);
  return match?.[1] ?? name;
}

export const TOOL_CLASSES: ToolClass[] = ['search', 'mutate', 'execute', 'delegate', 'network', 'other'];
