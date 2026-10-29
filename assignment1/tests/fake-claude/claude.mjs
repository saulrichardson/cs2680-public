#!/usr/bin/env node
// A stand-in for the Claude Code CLI. Replays a recorded fixture as stream-json.
//
// Which recording it replays — first match wins:
//   1. `FIXTURE:<name>` anywhere in the prompt -> fixtures/<name>.jsonl (names may carry digits,
//      e.g. `FIXTURE:synth-2000`)
//   2. FAKE_CLAUDE_FIXTURE                     -> that path
//   3. the prompt mentions a subagent           -> subagent-forward.jsonl
//   4. the prompt asks for a change (bug, add,  -> flat-allowed.jsonl (the allowlist recording,
//      update, implement, refactor)                whose edits arrive as structured Edit results)
//   5. the prompt is about max turns             -> failed.jsonl (a --max-turns overrun)
//   6. anything else                            -> flat.jsonl
// Rules 3 to 6 are what let ordinary, demo-shaped prompts drive this double unchanged — "find the
// bug that makes test_parse fail and fix it" and "use a subagent to survey this repository" land on
// the recordings of those very tasks — so a screenshot or a walkthrough never has to show a
// `FIXTURE:` tag. Two deliberate omissions: "fail" is not a keyword (the suite's own untagged
// prompts say "fix the failing test" and mean the ordinary run), and rule 6 stays `flat.jsonl`
// because untagged prompts in the tests assert that recording's numbers and card counts. A test
// that wants a specific recording says so with a `FIXTURE:` tag.
//
// Env: FAKE_CLAUDE_FIXTURE (a path, overriding rules 3 to 6), FAKE_CLAUDE_DELAY_MS (default 2).
// Prompt keywords for the failure paths: FAIL_EXIT -> exit 3 after two events, no result;
// NO_RESULT -> every event but the result, exit 0; GARBAGE -> a malformed line mid-stream;
// HANG -> the init line, then wait (SIGINT replays the real CLI's interrupt sequence and exits 0,
// SIGTERM exits 143).
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const get = (flag) => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
};
const prompt = get('-p') ?? '';
const resume = get('--resume');
const here = dirname(fileURLToPath(import.meta.url));
const fixtureFor = (name) => resolve(here, `../../fixtures/${name}.jsonl`);

/** The fixture an untagged prompt implies — see the rules at the top of this file. */
function guessFixture(text) {
  if (/subagent/i.test(text)) return 'subagent-forward';
  if (/\b(bug|add|adds|update|updates|implement|refactor)\b/i.test(text)) return 'flat-allowed';
  if (/\bmax[- ]?turns\b/i.test(text)) return 'failed';
  return 'flat';
}

const pick = /FIXTURE:([a-z0-9-]+)/.exec(prompt)?.[1];
const fixture = pick
  ? fixtureFor(pick)
  : (process.env.FAKE_CLAUDE_FIXTURE ?? fixtureFor(guessFixture(prompt)));
const delay = Number(process.env.FAKE_CLAUDE_DELAY_MS ?? 2);
const lines = readFileSync(fixture, 'utf8')
  .split('\n')
  .filter((l) => l.trim().length > 0);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function withSession(line) {
  if (!resume) return line;
  try {
    const o = JSON.parse(line);
    if (o.session_id) o.session_id = resume;
    return JSON.stringify(o);
  } catch {
    return line;
  }
}
const out = (line) => process.stdout.write(`${line}\n`);

const sessionIdOf = (line) => {
  try {
    const id = JSON.parse(line).session_id;
    return typeof id === 'string' ? id : undefined;
  } catch {
    return undefined;
  }
};

if (prompt.includes('HANG')) {
  const initLine = lines.find((l) => l.includes('"subtype":"init"')) ?? lines[0];
  out(withSession(initLine));
  process.on('SIGINT', () => {
    // The real CLI's interrupt sequence, as observed with 2.1.270 during the end-to-end QA pass: it
    // ends the turn with a `[Request interrupted by user]` user message and an
    // `error_during_execution` result whose only error is an internal `[ede_diagnostic]` string, not
    // with a clean success result. The console renders that pair as "Stopped by you", so the double
    // has to emit it — a success result would make a cancelled run look finished instead.
    // Same session id the init line carried, so every line of a hung run agrees.
    const session = resume ?? sessionIdOf(initLine) ?? 'hang-session';
    out(
      JSON.stringify({
        type: 'user',
        parent_tool_use_id: null,
        session_id: session,
        uuid: 'fake-interrupt',
        message: { role: 'user', content: [{ type: 'text', text: '[Request interrupted by user]' }] },
      }),
    );
    out(
      JSON.stringify({
        type: 'result',
        subtype: 'error_during_execution',
        is_error: true,
        errors: ['[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'],
        duration_ms: 1200,
        duration_api_ms: 900,
        num_turns: 1,
        total_cost_usd: 0.0123,
        session_id: session,
        usage: {},
        modelUsage: {},
        permission_denials: [],
      }),
    );
    process.exit(0);
  });
  process.on('SIGTERM', () => process.exit(143));
  setInterval(() => {}, 1000);
} else {
  const mode = prompt.includes('FAIL_EXIT')
    ? 'fail'
    : prompt.includes('NO_RESULT')
      ? 'noresult'
      : prompt.includes('GARBAGE')
        ? 'garbage'
        : 'ok';
  let n = 0;
  for (const line of lines) {
    const isResult = line.includes('"type":"result"');
    if (mode === 'noresult' && isResult) continue;
    if (mode === 'fail' && n === 2) process.exit(3);
    if (mode === 'garbage' && n === 3) out('{this is not json');
    out(withSession(line));
    n += 1;
    if (delay > 0) await sleep(delay);
  }
  await sleep(delay);
  process.exit(0);
}
