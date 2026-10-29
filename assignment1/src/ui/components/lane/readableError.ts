/** The server's bound on `maxTurns` (`z.number().int().min(1).max(200)` in the tRPC router). */
export const MAX_TURNS_LIMIT = 200;
export const MAX_TURNS_MESSAGE = `Max turns must be a whole number from 1 to ${MAX_TURNS_LIMIT}.`;

/** The composer's fields, in the words an operator would use for them. */
const FIELD_MESSAGES: Record<string, string> = {
  maxTurns: MAX_TURNS_MESSAGE,
  prompt: 'The prompt must be between 1 and 20,000 characters.',
  model: 'That model name is not one the CLI will accept.',
  laneId: 'This lane no longer exists — reload the board.',
};

interface ZodIssue {
  path?: unknown[];
  message?: unknown;
}

/**
 * A Zod input failure arrives as the stringified issue array (this router installs no
 * `errorFormatter`), which is developer output, not something to show an operator. Name the field
 * the issue is actually about — the previous version blamed max turns for every input error,
 * including a prompt the server refused — and fall back to the issue's own message when the field
 * is one this composer does not know about. Anything that is not an issue array is a real server
 * message and is shown as written.
 */
export function readableError(message: string): string {
  const text = message.trimStart();
  if (!text.startsWith('[')) return message;
  let issues: ZodIssue[];
  try {
    const parsed: unknown = JSON.parse(text);
    if (!Array.isArray(parsed)) return message;
    issues = parsed as ZodIssue[];
  } catch {
    return message;
  }
  const named = issues
    .map((issue) => (typeof issue.path?.[0] === 'string' ? FIELD_MESSAGES[issue.path[0]] : undefined))
    .filter((m): m is string => m !== undefined);
  if (named.length > 0) return [...new Set(named)].join(' ');
  const first = issues.find((issue) => typeof issue.message === 'string')?.message;
  return typeof first === 'string' ? first : 'The server refused this input.';
}
