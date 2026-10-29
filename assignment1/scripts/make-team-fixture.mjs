#!/usr/bin/env node
// Writes `fixtures/team-cafe.jsonl`: a session that delegates to three researchers, one of which
// delegates again, with Chrome tool calls in the parent and in the children.
//
// A generator rather than a hand-written file because the interesting part is the *shape* of the
// asynchronous delegation lifecycle — launch receipt, `task_started`, repeated `task_progress`,
// `task_notification` — and that is 120 lines of near-identical JSON nobody should maintain by hand.
// The payload fields follow `docs/reference/claude-stream-json-types.md`, which is written from the
// 2.1.270 wire; the committed `subagent*.jsonl` fixtures are real recordings of the *synchronous*
// shape and stay as they are.
//
// Run with: pnpm fixtures:team
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const CHROME = 'mcp__claude-in-chrome__';
const SESSION = 'cafe-session-0001';
const lines = [];
let uuid = 0;
const push = (event) => lines.push(JSON.stringify({ uuid: `u${++uuid}`, session_id: SESSION, ...event }));

const text = (t, parent = null) =>
  push({
    type: 'assistant',
    parent_tool_use_id: parent,
    message: { id: `m${uuid}`, content: [{ type: 'text', text: t }] },
  });

const call = (id, name, input, parent = null) =>
  push({
    type: 'assistant',
    parent_tool_use_id: parent,
    message: { id: `m${uuid}`, content: [{ type: 'tool_use', id, name, input }] },
  });

const result = (id, content, parent = null, extra = {}) =>
  push({
    type: 'user',
    parent_tool_use_id: parent,
    message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content }] },
    tool_use_result: extra,
  });

const browse = (id, tool, url, parent = null) => {
  call(id, `${CHROME}${tool}`, { url }, parent);
  result(id, `<html><title>${url}</title></html>`, parent, { url, title: url });
};

const delegate = (id, taskId, description, prompt, agentType, parent = null) => {
  call(id, 'Agent', { description, prompt, subagent_type: agentType }, parent);
  result(id, 'Async agent launched successfully. (This tool result is internal metadata…)', null, {
    isAsync: true,
    status: 'async_launched',
    agentId: taskId,
    description,
    prompt,
  });
  push({
    type: 'system',
    subtype: 'task_started',
    task_id: taskId,
    tool_use_id: id,
    description,
    subagent_type: agentType,
    task_type: 'local_agent',
    is_backgrounded: true,
    spawn_depth: parent ? 2 : 1,
    prompt,
  });
};

const progress = (id, taskId, description, tools, tokens, ms, lastTool) =>
  push({
    type: 'system',
    subtype: 'task_progress',
    task_id: taskId,
    tool_use_id: id,
    description,
    usage: { total_tokens: tokens, tool_uses: tools, duration_ms: ms },
    last_tool_name: lastTool,
  });

const finish = (id, taskId, summary, tools, tokens, ms) =>
  push({
    type: 'system',
    subtype: 'task_notification',
    task_id: taskId,
    tool_use_id: id,
    status: 'completed',
    summary,
    usage: { total_tokens: tokens, tool_uses: tools, duration_ms: ms },
  });

push({
  type: 'system',
  subtype: 'init',
  cwd: '/tmp/cafe',
  model: 'claude-opus-5',
  permissionMode: 'acceptEdits',
  claude_code_version: '2.1.270',
  apiKeySource: 'none',
  mcp_servers: [{ name: 'claude-in-chrome', status: 'connected' }],
  tools: [
    'Bash',
    'Read',
    'Edit',
    'Write',
    'Glob',
    'Grep',
    'Agent',
    `${CHROME}navigate`,
    `${CHROME}read_page`,
    `${CHROME}browser_batch`,
    `${CHROME}tabs_context_mcp`,
  ],
});

text('Planning the fit-out study. I will check the official sources in the browser first.');
browse('toolu_p1', 'navigate', 'https://www.example.gov/zoning/food-service');

const team = [
  [
    'toolu_a1',
    'task_zoning',
    'Zoning and use permits',
    'Read the municipal code for a food-service change of use.',
    'Explore',
  ],
  [
    'toolu_a2',
    'task_access',
    'Accessible route and restrooms',
    'Check the accessibility standard for a 60 m² cafe.',
    'Explore',
  ],
  [
    'toolu_a3',
    'task_mep',
    'Plumbing and electrical loads',
    'Estimate service loads and fixture counts.',
    'general-purpose',
  ],
];
for (const [id, taskId, description, prompt, agentType] of team)
  delegate(id, taskId, description, prompt, agentType);

// Each researcher forwards a little of its own work, and drives the browser itself.
push({
  type: 'user',
  parent_tool_use_id: 'toolu_a1',
  message: {
    role: 'user',
    content: [{ type: 'text', text: 'Read the municipal code for a food-service change of use.' }],
  },
});
browse('toolu_a1_b1', 'navigate', 'https://www.example.gov/code/article-8', 'toolu_a1');
progress('toolu_a1', 'task_zoning', 'Reading the use table', 1, 12000, 8000, `${CHROME}navigate`);
text('Article 8 lists food service as permitted with a review.', 'toolu_a1');

browse('toolu_a2_b1', 'navigate', 'https://www.example.gov/accessibility/restrooms', 'toolu_a2');
progress('toolu_a2', 'task_access', 'Checking clearances', 1, 9000, 6000, `${CHROME}navigate`);

// The third researcher delegates again: a nested node, which the band draws as a branch.
delegate(
  'toolu_a3_n1',
  'task_grease',
  'Grease interceptor sizing',
  'Size the interceptor for two sinks.',
  'Explore',
  'toolu_a3',
);
progress('toolu_a3', 'task_mep', 'Waiting on the interceptor figure', 2, 15000, 11000, 'Agent');
browse('toolu_a3_n1_b1', 'navigate', 'https://www.example.gov/plumbing/interceptors', 'toolu_a3_n1');
finish(
  'toolu_a3_n1',
  'task_grease',
  '## Interceptor\n\n50 gallons per minute for two sinks.',
  3,
  8000,
  22000,
);

finish(
  'toolu_a1',
  'task_zoning',
  '## Zoning\n\nFood service is permitted with design review. Source: article 8.',
  6,
  41000,
  61000,
);
finish(
  'toolu_a2',
  'task_access',
  '## Accessibility\n\nOne accessible restroom, 1500 mm turning circle.',
  5,
  32000,
  54000,
);
finish(
  'toolu_a3',
  'task_mep',
  '## Services\n\n60 A three-phase supply; two sinks on a 50 gpm interceptor.',
  9,
  58000,
  96000,
);

call('toolu_w1', 'Write', { file_path: '/tmp/cafe/HANDOFF.md', content: '# Handoff\n' });
result('toolu_w1', 'File created successfully at: /tmp/cafe/HANDOFF.md', null, {
  type: 'create',
  filePath: '/tmp/cafe/HANDOFF.md',
  content: '# Handoff\n',
});

// The final response names its own outputs with relative links, which is what the workspace link
// resolution has to turn into openable files rather than console routes.
text(
  [
    '## Done',
    '',
    'Saved the package:',
    '',
    '- [Handoff index](HANDOFF.md)',
    '- [Plumbing brief](proposals/plumbing.md)',
    '- [Layout drawing](designs/layout.svg)',
    '',
    'Source: [article 8](https://www.example.gov/code/article-8).',
  ].join('\n'),
);

push({
  type: 'result',
  subtype: 'success',
  is_error: false,
  duration_ms: 184000,
  duration_api_ms: 96000,
  num_turns: 14,
  total_cost_usd: 1.37,
  session_id: SESSION,
  usage: {
    input_tokens: 42,
    output_tokens: 8100,
    cache_read_input_tokens: 240000,
    cache_creation_input_tokens: 9000,
  },
  modelUsage: {
    'claude-opus-5': {
      inputTokens: 42,
      outputTokens: 8100,
      cacheReadInputTokens: 240000,
      cacheCreationInputTokens: 9000,
      costUSD: 1.37,
      contextWindow: 200000,
    },
  },
});

const out = resolve(dirname(fileURLToPath(import.meta.url)), '../fixtures/team-cafe.jsonl');
writeFileSync(out, `${lines.join('\n')}\n`);
console.log(`wrote ${lines.length} lines to ${out}`);
