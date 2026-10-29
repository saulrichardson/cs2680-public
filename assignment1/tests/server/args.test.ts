import { describe, expect, it } from 'vitest';
import {
  buildClaudeArgs,
  CHROME_ALLOWED_TOOLS,
  DEFAULT_ALLOWED_TOOLS,
  sanitizeEnv,
} from '@/server/process/args';

const base = { runId: 'r', laneId: 'l', prompt: 'do it', cwd: '/tmp', permission: 'allowlist' as const };

describe('buildClaudeArgs', () => {
  it('allowlist strategy', () => {
    expect(buildClaudeArgs(base, DEFAULT_ALLOWED_TOOLS)).toEqual([
      '-p',
      'do it',
      '--output-format',
      'stream-json',
      '--verbose',
      '--forward-subagent-text',
      '--chrome',
      '--permission-mode',
      'acceptEdits',
      '--allowedTools',
      `${DEFAULT_ALLOWED_TOOLS},${CHROME_ALLOWED_TOOLS}`,
    ]);
  });
  it('bypass strategy, resume, max turns, model', () => {
    expect(
      buildClaudeArgs(
        { ...base, permission: 'bypass', resumeSessionId: 'sess', maxTurns: 2, model: 'sonnet' },
        DEFAULT_ALLOWED_TOOLS,
      ),
    ).toEqual([
      '-p',
      'do it',
      '--output-format',
      'stream-json',
      '--verbose',
      '--forward-subagent-text',
      '--chrome',
      '--dangerously-skip-permissions',
      '--resume',
      'sess',
      '--max-turns',
      '2',
      '--model',
      'sonnet',
    ]);
  });

  /**
   * The browser is execution policy: there is no input that turns it off, so there is no command
   * line without it. `--chrome` is still stated rather than omitted, because omitting it means
   * "inherit whatever this machine is configured to do".
   */
  it('always launches with the browser, whatever it is asked for', () => {
    for (const input of [base, { ...base, permission: 'bypass' as const }, { ...base, maxTurns: 3 }]) {
      const args = buildClaudeArgs(input, DEFAULT_ALLOWED_TOOLS);
      expect(args).toContain('--chrome');
      expect(args).not.toContain('--no-chrome');
    }
    // No property of `StartRunInput` can ask for anything else: nothing here reads one.
    expect(JSON.stringify(buildClaudeArgs({ ...base }, DEFAULT_ALLOWED_TOOLS))).toContain('--chrome');
  });

  it('allows the Chrome MCP server, and only that server, alongside the standard tools', () => {
    const args = buildClaudeArgs(base, DEFAULT_ALLOWED_TOOLS);
    // The whole server, not a fixed list of its tools: the CLI can add a browser tool and it must
    // not be silently denied the day it ships.
    expect(args[args.indexOf('--allowedTools') + 1]).toBe(`${DEFAULT_ALLOWED_TOOLS},${CHROME_ALLOWED_TOOLS}`);
    expect(CHROME_ALLOWED_TOOLS).toBe('mcp__claude-in-chrome');

    // Under bypass there is no allowlist to extend, and the flag still has to be there.
    const bypass = buildClaudeArgs({ ...base, permission: 'bypass' }, DEFAULT_ALLOWED_TOOLS);
    expect(bypass).toContain('--chrome');
    expect(bypass).not.toContain('--allowedTools');
  });
});

describe('sanitizeEnv', () => {
  it('drops API keys and nested-session markers, keeps the rest', () => {
    const env = sanitizeEnv({ PATH: '/bin', ANTHROPIC_API_KEY: 'sk-x', CLAUDECODE: '1', HOME: '/h' });
    expect(env).toEqual({ PATH: '/bin', HOME: '/h' });
  });

  it('drops every credential and endpoint override the child must not inherit', () => {
    const dropped = [
      'ANTHROPIC_API_KEY',
      'ANTHROPIC_AUTH_TOKEN',
      'ANTHROPIC_BASE_URL',
      'ANTHROPIC_API_URL',
      'ANTHROPIC_CUSTOM_HEADERS',
      'CLAUDE_CODE_OAUTH_TOKEN',
      'CLAUDE_CODE_USE_BEDROCK',
      'CLAUDE_CODE_USE_VERTEX',
      'CLAUDECODE',
    ];
    const env = sanitizeEnv({
      ...Object.fromEntries(dropped.map((k) => [k, 'x'])),
      PATH: '/bin',
      AWS_PROFILE: 'work',
      AWS_REGION: 'us-east-1',
    });
    for (const name of dropped) expect(env).not.toHaveProperty(name);
    // AWS_* stays: the agent's own tools may need it
    expect(env).toEqual({ PATH: '/bin', AWS_PROFILE: 'work', AWS_REGION: 'us-east-1' });
  });

  it('drops the launching session state by prefix, keeping non-credential ANTHROPIC_* settings', () => {
    // What a shell inside the Claude Code desktop app actually exports (QA: 18 of these reached the
    // child), plus the two names a prefix rule has to keep.
    const env = sanitizeEnv({
      CLAUDE_CODE_SESSION_ID: 's-1',
      CLAUDE_CODE_CHILD_SESSION: '1',
      CLAUDE_CODE_MESSAGING_SOCKET: '/tmp/sock',
      CLAUDE_CODE_MESSAGING_TOKEN: 'tok',
      CLAUDE_CODE_HOST_SESSION_ID: 's-0',
      CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH: '1',
      CLAUDE_CODE_EXECPATH: '/opt/claude',
      CLAUDE_CODE_BRAND_NEW_VARIABLE: '1',
      CLAUDECODE_NESTED: '1',
      ANTHROPIC_MODEL: 'claude-opus-5[1m]',
      PATH: '/bin',
    });
    expect(Object.keys(env).filter((k) => k.startsWith('CLAUDE'))).toEqual([]);
    expect(env).toEqual({ ANTHROPIC_MODEL: 'claude-opus-5[1m]', PATH: '/bin' });
  });
});
