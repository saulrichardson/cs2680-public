import { CHROME_MCP_SERVER, type PermissionStrategy } from '@/core/types';

export interface StartRunInput {
  runId: string;
  laneId: string;
  prompt: string;
  cwd: string;
  permission: PermissionStrategy;
  resumeSessionId?: string;
  maxTurns?: number;
  model?: string;
  groupId?: string;
  resumedFrom?: string;
}

export const DEFAULT_ALLOWED_TOOLS =
  'Bash,Read,Edit,Write,MultiEdit,NotebookEdit,Glob,Grep,LS,Agent,Task,WebFetch,WebSearch,TodoWrite,Skill';

/**
 * The permission entry that covers every tool the Chrome extension exposes.
 *
 * The extension is an MCP server, so its tools are `mcp__claude-in-chrome__<tool>` and the set is
 * the CLI's to change. `mcp__<server>` is Claude Code's own way of writing "this whole server",
 * which keeps a new browser tool from being silently denied the day it ships. It widens nothing
 * else: no other MCP server is named here.
 */
export const CHROME_ALLOWED_TOOLS = `mcp__${CHROME_MCP_SERVER}`;

/**
 * The command line for one run.
 *
 * **The browser is execution policy, not a setting.** Every execution this console starts is
 * launched with `--chrome` and the extension's tools allowed, because an operator who has to find
 * and flip a switch before the agent can look something up is doing the console's job for it. The
 * flag is stated rather than omitted for the same reason it always was: `--chrome` can also be
 * enabled globally in the operator's own Claude Code settings, so omitting it means "inherit this
 * machine's configuration", which is not something the console could then report honestly.
 *
 * What this does *not* claim is that a browser is there. The extension may be uninstalled, Chrome
 * may be shut, the CLI may be signed out. The run's own `system/init` says whether the browser
 * tools loaded and its trajectory says whether any call worked (`deriveBrowserView`); ordinary
 * non-browser work is unaffected either way, because a tool the model never calls costs nothing.
 *
 * One related constraint: `sanitizeEnv` drops `ANTHROPIC_API_KEY` and friends, and the Chrome
 * integration requires an OAuth login — an API-key session has it disabled even with `--chrome`.
 */
export function buildClaudeArgs(input: StartRunInput, allowedTools: string): string[] {
  const args = ['-p', input.prompt, '--output-format', 'stream-json', '--verbose', '--forward-subagent-text'];
  args.push('--chrome');
  if (input.permission === 'bypass') args.push('--dangerously-skip-permissions');
  else
    args.push(
      '--permission-mode',
      'acceptEdits',
      '--allowedTools',
      `${allowedTools},${CHROME_ALLOWED_TOOLS}`,
    );
  if (input.resumeSessionId) args.push('--resume', input.resumeSessionId);
  if (input.maxTurns !== undefined) args.push('--max-turns', String(input.maxTurns));
  if (input.model) args.push('--model', input.model);
  return args;
}

/**
 * Credentials and endpoint overrides the child must not inherit from the server: an API key or
 * OAuth token would bill this process's account instead of the user's own CLI login, and a
 * redirected base URL would silently send the run somewhere else.
 * `ANTHROPIC_*` variables that only *configure* a run (`ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`…)
 * are not credentials and stay, as does `AWS_*`: the agent's own tools may need it.
 */
const DROP = new Set([
  'ANTHROPIC_API_KEY',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_API_URL',
  'ANTHROPIC_CUSTOM_HEADERS',
]);

/**
 * Everything the launching Claude Code session put in the environment, by prefix.
 *
 * Start Mission Control from a terminal *inside* Claude Code (the desktop app's own terminal) and
 * the shell exports around twenty of these — `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`,
 * `CLAUDE_CODE_MESSAGING_SOCKET` and its token, `CLAUDE_CODE_HOST_SESSION_ID`,
 * `CLAUDE_CODE_EXECPATH`, … — so a list of known names always lags behind. Handing them to the
 * child would give it another session's messaging socket and session ids: it would talk to the
 * wrong session instead of being a plain agent of its own. `CLAUDECODE` marks "already inside
 * Claude Code" and changes the CLI's own behaviour, so it goes too (with any `CLAUDECODE_*`).
 */
const DROP_PREFIXES = ['CLAUDE_CODE_', 'CLAUDECODE'];

// Parameter is intentionally broader than `NodeJS.ProcessEnv`: Next.js augments that interface to
// require a non-optional `NODE_ENV`, which a plain test fixture object won't have. The return type
// stays `NodeJS.ProcessEnv` since that's what `spawn`'s `env` option expects.
export function sanitizeEnv(env: Record<string, string | undefined>): NodeJS.ProcessEnv {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined || DROP.has(k) || DROP_PREFIXES.some((p) => k.startsWith(p))) continue;
    out[k] = v;
  }
  return out as NodeJS.ProcessEnv;
}
