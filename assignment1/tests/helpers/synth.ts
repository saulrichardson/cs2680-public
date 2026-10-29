import type { Envelope } from '@/core/types';

export interface SynthOptions {
  runId?: string;
  laneId?: string;
  calls: number; // number of tool calls; each produces 2 assistant blocks + 1 result = 3 events, plus text
  subagentEvery?: number; // every Nth call is an Agent with 3 nested Bash calls
  startTs?: number;
}

/** Deterministic synthetic stream shaped like the real one. ~4 events per call. */
export function synthEnvelopes(opts: SynthOptions): Envelope[] {
  const runId = opts.runId ?? 'run-synth';
  const laneId = opts.laneId ?? 'lane-synth';
  let ts = opts.startTs ?? Date.parse('2026-09-16T12:00:00Z');
  let seq = 0;
  let firstTs: number | undefined;
  const out: Envelope[] = [];
  const push = (event: Record<string, unknown>) => {
    ts += 50;
    firstTs ??= ts;
    seq += 1;
    out.push({
      laneId,
      runId,
      seq,
      receivedAt: ts,
      event: { timestamp: new Date(ts).toISOString(), ...event } as Envelope['event'],
    });
  };
  const tool = (
    id: string,
    name: string,
    input: Record<string, unknown>,
    parent: string | null,
    messageId: string,
  ) =>
    push({
      type: 'assistant',
      parent_tool_use_id: parent,
      message: {
        id: messageId,
        model: 'claude-opus-5',
        content: [{ type: 'tool_use', id, name, input }],
        usage: { input_tokens: 2, cache_read_input_tokens: 40_000 + seq, cache_creation_input_tokens: 500 },
      },
    });
  const result = (id: string, parent: string | null, text: string) =>
    push({
      type: 'user',
      parent_tool_use_id: parent,
      message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content: text }] },
      tool_use_result: { stdout: text, stderr: '' },
    });

  push({
    type: 'system',
    subtype: 'init',
    session_id: 'synth-session',
    cwd: '/tmp/synth',
    model: 'claude-opus-5',
    tools: ['Bash', 'Read', 'Edit', 'Agent'],
    permissionMode: 'acceptEdits',
    claude_code_version: '2.1.270',
  });
  for (let i = 0; i < opts.calls; i += 1) {
    const mid = `msg_${i}`;
    push({
      type: 'assistant',
      parent_tool_use_id: null,
      message: { id: mid, content: [{ type: 'text', text: `Step ${i}: looking at **file ${i}**.` }] },
    });
    const isAgent = opts.subagentEvery ? (i + 1) % opts.subagentEvery === 0 : false;
    const id = `toolu_${i}`;
    if (isAgent) {
      tool(
        id,
        'Agent',
        { description: `Survey part ${i}`, prompt: 'survey', subagent_type: 'Explore' },
        null,
        mid,
      );
      push({
        type: 'user',
        parent_tool_use_id: id,
        message: { role: 'user', content: [{ type: 'text', text: 'survey this' }] },
      });
      for (let k = 0; k < 3; k += 1) {
        const cid = `${id}_c${k}`;
        tool(cid, 'Bash', { command: `ls part${k}` }, id, `${mid}_sub${k}`);
        result(cid, id, `out ${k}\n`.repeat(20));
      }
      result(id, null, 'Survey complete.');
    } else {
      const name = i % 3 === 0 ? 'Read' : i % 3 === 1 ? 'Bash' : 'Edit';
      const input =
        name === 'Read'
          ? { file_path: `/tmp/synth/f${i}.py` }
          : name === 'Bash'
            ? { command: `pytest -k t${i}`, description: `run test ${i}` }
            : { file_path: `/tmp/synth/f${i}.py`, old_string: 'a', new_string: 'b' };
      tool(id, name, input, null, mid);
      result(id, null, `line\n`.repeat(30));
    }
  }
  // The run lasted from its first event to its last, which is this result event itself — pushed
  // 50 ms after the previous one. Measuring from `opts.startTs ?? 0` instead reported the whole
  // Unix epoch (a 56-year "run") whenever the caller left `startTs` at its default.
  const durationMs = ts + 50 - (firstTs ?? ts + 50);
  push({
    type: 'result',
    subtype: 'success',
    is_error: false,
    duration_ms: durationMs,
    duration_api_ms: 1000,
    num_turns: opts.calls,
    total_cost_usd: 0.01 * opts.calls,
    session_id: 'synth-session',
    usage: {
      input_tokens: 10,
      output_tokens: 100 * opts.calls,
      cache_read_input_tokens: 1000,
      cache_creation_input_tokens: 100,
    },
    modelUsage: {
      'claude-opus-5': {
        inputTokens: 10,
        outputTokens: 100 * opts.calls,
        cacheReadInputTokens: 1000,
        cacheCreationInputTokens: 100,
        costUSD: 0.01 * opts.calls,
        contextWindow: 200000,
        webSearchRequests: 0,
        maxOutputTokens: 64000,
      },
    },
  });
  return out;
}
