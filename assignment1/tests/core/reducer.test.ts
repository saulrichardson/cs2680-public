import { describe, expect, it } from 'vitest';
import { deriveTimeline, summarizeRun } from '@/core/derive';
import { applyEnvelopes, applyEvent, applyLifecycle, createRunView } from '@/core/reducer';
import type { Block, Envelope, RunView, ToolCall } from '@/core/types';
import { loadFixture } from '../helpers/fixtures';

function fresh(name: string, prompt = 'test prompt'): RunView {
  return createRunView({
    runId: `run-${name}`,
    laneId: 'lane-fixture',
    prompt,
    cwd: '/tmp/example-project',
    startedAt: Date.parse('2026-09-16T16:00:00Z'),
  });
}
function reduce(name: 'flat' | 'subagent' | 'failed' | 'flat-allowed' | 'subagent-forward'): RunView {
  return applyEnvelopes(fresh(name), loadFixture(name));
}
function calls(view: RunView): ToolCall[] {
  return Object.values(view.callsById);
}
function kinds(blocks: Block[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const b of blocks) out[b.kind] = (out[b.kind] ?? 0) + 1;
  return out;
}

describe('createRunView', () => {
  it('starts running with an empty trajectory', () => {
    const v = fresh('x');
    expect(v.status).toBe('running');
    expect(v.blocks).toEqual([]);
    expect(v.callsById).toEqual({});
    expect(v.eventCount).toBe(0);
  });
});

describe('flat fixture', () => {
  const view = reduce('flat');
  it('pairs every tool call with its result', () => {
    const cs = calls(view);
    expect(cs).toHaveLength(10);
    expect(cs.filter((c) => c.name === 'Bash')).toHaveLength(9);
    expect(cs.filter((c) => c.name === 'Skill')).toHaveLength(1);
    // one Bash call fails (exit code 1, "No module named logparse") per fixtures/flat.jsonl
    expect(cs.filter((c) => c.status === 'done')).toHaveLength(9);
    expect(cs.filter((c) => c.status === 'error')).toHaveLength(1);
    expect(cs.every((c) => typeof c.durationMs === 'number' && c.durationMs >= 0)).toBe(true);
    expect(cs.every((c) => c.parentToolUseId === null)).toBe(true);
  });
  it('lays out main-thread blocks in order with the expected kinds', () => {
    expect(kinds(view.blocks)).toEqual({ text: 10, thinking: 7, tool: 10, notice: 1 });
    const first = view.blocks[0];
    expect(first?.kind).toBe('text');
  });
  it('records the result numbers, session, model, and outcome, and leaves the status to the lifecycle', () => {
    expect(view.status).toBe('running');
    expect(view.endedAt).toBeUndefined();
    expect(view.outcome).toEqual({ subtype: 'success', isError: false });
    expect(view.numbers).toEqual({
      costUsd: 0.7716919999999999,
      durationMs: 81598,
      durationApiMs: 77768,
      numTurns: 12,
    });
    expect(view.sessionId).toBe('066df07f-cf42-4d5a-b156-e83d817ad021');
    expect(view.model).toBe('claude-opus-5[1m]');
    expect(view.usage?.outputTokens).toBe(6023);
    expect(view.usage?.thinkingTokens).toBe(2140);
    expect(view.usage?.perModel['claude-opus-5[1m]']?.contextWindow).toBe(1000000);
  });
  it('tracks live context tokens from the latest main-thread message', () => {
    // last assistant message in flat: input 2 + cache_read 43127 + cache_creation 561
    expect(view.context.tokens).toBe(2 + 43127 + 561);
  });
  it('records setup, activity, and post-turn summary', () => {
    expect(view.setup.claudeCodeVersion).toBe('2.1.270');
    expect(view.setup.permissionMode).toBe('bypassPermissions');
    expect(view.setup.hooks).toHaveLength(1);
    expect(view.setup.hooks[0]?.outcome).toBe('success');
    expect(view.activity).toBe('test_parse bug: quote-unaware split on spaces; fixed tokenizer');
    expect(view.summary?.category).toBe('completed');
    expect(view.thinking).toBeUndefined();
    expect(view.unparsedCount).toBe(0);
  });
  it('captures a Bash edit as patches', () => {
    const withPatches = calls(view).filter((c) => c.patches.length > 0);
    expect(withPatches).toHaveLength(1);
    expect(withPatches[0]?.patches[0]?.filePath).toMatch(/parser\.py$/);
  });
});

describe('subagent fixture', () => {
  const view = reduce('subagent');
  it('nests the subagent under its Agent call', () => {
    const agent = calls(view).find((c) => c.name === 'Agent');
    expect(agent).toBeDefined();
    expect(agent?.toolClass).toBe('delegate');
    expect(agent?.status).toBe('done');
    expect(kinds(agent?.children ?? [])).toEqual({ 'subagent-prompt': 1, tool: 8 });
    const childCalls = calls(view).filter((c) => c.parentToolUseId === agent?.id);
    expect(childCalls).toHaveLength(8);
    expect(childCalls.every((c) => c.name === 'Bash' && c.status === 'done')).toBe(true);
  });
  it('carries task lifecycle info onto the Agent call', () => {
    const agent = calls(view).find((c) => c.name === 'Agent');
    expect(agent?.task?.subagentType).toBe('Explore');
    expect(agent?.task?.status).toBe('completed');
    expect(agent?.task?.description).toBe('Survey repository structure');
    expect(agent?.task?.toolUses).toBeGreaterThanOrEqual(8);
  });
  it('counts API retries as notices and finishes', () => {
    expect(view.retries).toBe(3);
    expect(view.blocks.filter((b) => b.kind === 'notice' && b.level === 'warning')).toHaveLength(3);
    expect(view.outcome?.isError).toBe(false);
    expect(view.numbers?.numTurns).toBe(6);
  });
});

describe('failed fixture', () => {
  const view = reduce('failed');
  it('reports the max-turns error and the numbers, and still leaves the status to the lifecycle', () => {
    expect(view.outcome).toEqual({ subtype: 'error_max_turns', isError: true });
    expect(view.status).toBe('running');
    expect(view.error?.message).toContain('Reached maximum number of turns');
    expect(view.numbers?.numTurns).toBe(3);
    expect(view.numbers?.costUsd).toBeCloseTo(0.177, 2);
  });
  it('pairs two parallel Bash calls that share a message id', () => {
    const bash = calls(view).filter((c) => c.name === 'Bash');
    expect(bash).toHaveLength(2);
    expect(bash[0]?.messageId).toBe(bash[1]?.messageId);
    expect(bash.every((c) => c.status === 'done')).toBe(true);
  });
});

describe('allowlist fixtures', () => {
  it('flat-allowed uses Read and Edit and attaches Edit patches', () => {
    const view = reduce('flat-allowed');
    const cs = calls(view);
    expect(cs.filter((c) => c.name === 'Read')).toHaveLength(4);
    const edits = cs.filter((c) => c.name === 'Edit');
    expect(edits).toHaveLength(2);
    expect(edits.every((c) => c.patches.length >= 1)).toBe(true);
    expect(view.outcome).toEqual({ subtype: 'success', isError: false });
  });
  it('subagent-forward nests forwarded subagent text under the Agent call', () => {
    const view = reduce('subagent-forward');
    const agent = calls(view).find((c) => c.name === 'Agent');
    const k = kinds(agent?.children ?? []);
    expect(k['subagent-prompt']).toBe(1);
    expect(k.text).toBe(6);
    expect(k.tool).toBe(11);
  });
});

describe('robustness', () => {
  const base = fresh('r');
  const env = (event: Record<string, unknown>, seq = 1): Envelope => ({
    laneId: 'l',
    runId: 'run-r',
    seq,
    receivedAt: 1000 + seq,
    event: { type: 'x', ...event } as Envelope['event'],
  });

  it('replaces an intermediate result error with the final successful result', () => {
    const intermediate = applyEvent(
      base,
      env(
        {
          type: 'result',
          subtype: 'error_max_turns',
          is_error: true,
          errors: ['Reached maximum number of turns (18)'],
          num_turns: 19,
        },
        1,
      ),
    );
    expect(intermediate.error?.message).toContain('maximum');
    const final = applyEvent(
      intermediate,
      env({ type: 'result', subtype: 'success', is_error: false, num_turns: 6 }, 2),
    );
    expect(final.outcome).toEqual({ subtype: 'success', isError: false });
    expect(final.numbers?.numTurns).toBe(6);
    expect(final.error).toBeUndefined();
  });

  it('does not mutate its input', () => {
    const before = JSON.stringify(base);
    applyEvent(
      base,
      env({ type: 'system', subtype: 'thinking_tokens', estimated_tokens: 5, estimated_tokens_delta: 5 }),
    );
    expect(JSON.stringify(base)).toBe(before);
  });
  it('does not mutate a mid-run view (with nested calls/blocks) when applying more envelopes', () => {
    const envelopes = loadFixture('flat');
    const mid = applyEnvelopes(fresh('flat'), envelopes.slice(0, 20));
    const before = JSON.stringify(mid);
    applyEnvelopes(mid, envelopes.slice(20));
    expect(JSON.stringify(mid)).toBe(before);
  });
  it('records an unparsed line as a block', () => {
    const v = applyEvent(base, env({ type: 'unparsed', raw: '{bad', error: 'Unexpected token' }));
    expect(v.unparsedCount).toBe(1);
    expect(v.blocks[0]?.kind).toBe('unparsed');
  });
  it('tolerates a tool_result whose call was never seen', () => {
    const v = applyEvent(
      base,
      env({
        type: 'user',
        message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'ghost', content: 'x' }] },
        parent_tool_use_id: null,
      }),
    );
    expect(v.blocks.some((b) => b.kind === 'notice')).toBe(true);
    expect(Object.keys(v.callsById)).toHaveLength(0);
  });
  it('counts unknown event types', () => {
    const v = applyEvent(base, env({ type: 'brand_new_thing' }));
    expect(v.ignoredCount).toBe(1);
    expect(v.eventCount).toBe(1);
  });
  it('stamps blocks with the envelope receivedAt, never the recorded event timestamp', () => {
    // One clock per run: a recorded `timestamp` is the CLI's clock at recording time, which for a
    // replay, an import or a fixture-driven run sits hours away from this run's own axis. It stays
    // in the raw event but must not reach the view model.
    const withTs = applyEvent(
      base,
      env({
        type: 'assistant',
        timestamp: '2026-09-16T16:28:35.948Z',
        message: { id: 'm1', content: [{ type: 'text', text: 'hi' }] },
        parent_tool_use_id: null,
      }),
    );
    expect((withTs.blocks[0] as { ts: number }).ts).toBe(1001); // env(seq = 1) -> receivedAt 1001
    expect((withTs.blocks[0] as { ts: number }).ts).not.toBe(Date.parse('2026-09-16T16:28:35.948Z'));
    const withoutTs = applyEvent(
      base,
      env(
        {
          type: 'assistant',
          message: { id: 'm1', content: [{ type: 'text', text: 'hi' }] },
          parent_tool_use_id: null,
        },
        7,
      ),
    );
    expect((withoutTs.blocks[0] as { ts: number }).ts).toBe(1007);
  });
});

describe('async Agent (delegate) calls', () => {
  // The real shapes from CLI 2.1.270: the Agent tool_result comes back in milliseconds with nothing
  // but harness bookkeeping, and the subagent's own lifecycle arrives as `task_*` system events.
  const AGENT_ID = 'toolu_async1';
  const launchText =
    'Async agent launched successfully. (This tool result is internal metadata — never quote it.)';
  const summary = '## Repository survey\n\nThree modules, one failing test.';
  const stream: Record<string, unknown>[] = [
    {
      type: 'assistant',
      parent_tool_use_id: null,
      message: {
        id: 'msg_1',
        model: 'claude-opus-5[1m]',
        content: [
          {
            type: 'tool_use',
            id: AGENT_ID,
            name: 'Agent',
            input: {
              description: 'Survey repository structure',
              prompt: 'Survey the repo',
              subagent_type: 'Explore',
            },
          },
        ],
      },
    },
    {
      type: 'user',
      parent_tool_use_id: null,
      message: {
        role: 'user',
        content: [{ type: 'tool_result', tool_use_id: AGENT_ID, content: launchText }],
      },
      tool_use_result: {
        isAsync: true,
        status: 'async_launched',
        agentId: 'task-1',
        description: 'Survey repository structure',
        resolvedModel: 'claude-opus-5[1m]',
        prompt: 'Survey the repo',
      },
    },
    {
      type: 'system',
      subtype: 'task_started',
      task_id: 'task-1',
      tool_use_id: AGENT_ID,
      description: 'Survey repository structure',
      subagent_type: 'Explore',
      is_backgrounded: true,
      spawn_depth: 1,
      prompt: 'Survey the repo',
    },
    {
      type: 'system',
      subtype: 'task_progress',
      task_id: 'task-1',
      tool_use_id: AGENT_ID,
      description: 'Running Read all source modules',
      subagent_type: 'Explore',
      usage: { total_tokens: 22_000, tool_uses: 18, duration_ms: 35_000 },
      last_tool_name: 'Read',
    },
    {
      type: 'system',
      subtype: 'task_notification',
      task_id: 'task-1',
      tool_use_id: AGENT_ID,
      status: 'completed',
      summary,
      output_file: '/tmp/report.md',
    },
  ];
  const at = (n: number): RunView =>
    applyEnvelopes(
      fresh('async'),
      stream.slice(0, n).map((event, i) => ({
        laneId: 'lane-fixture',
        runId: 'run-async',
        seq: i + 1,
        receivedAt: 1000 + i * 1000,
        event: event as Envelope['event'],
      })),
    );

  it('keeps the call pending on the launch receipt and marks that text internal', () => {
    const view = at(2);
    const agent = calls(view)[0] as ToolCall;
    expect(agent.status).toBe('pending');
    expect(agent.durationMs).toBeUndefined();
    expect(agent.result).toMatchObject({ text: launchText, internal: true, isError: false });
    expect(agent.task).toMatchObject({ async: true, status: 'running' });
  });

  it('draws a pending async call up to now, not to its launch receipt', () => {
    const view = at(2);
    const bar = deriveTimeline(view, 99_000)[0];
    expect(bar?.start).toBe(1000);
    expect(bar?.end).toBe(99_000); // not 2000, the launch receipt's ts
  });

  it('keeps the original description and tracks live progress separately', () => {
    const task = (calls(at(4))[0] as ToolCall).task;
    expect(task?.description).toBe('Survey repository structure');
    expect(task?.activity).toBe('Running Read all source modules');
    expect(task).toMatchObject({
      subagentType: 'Explore',
      spawnDepth: 1,
      totalTokens: 22_000,
      toolUses: 18,
      durationMs: 35_000,
      lastToolName: 'Read',
      status: 'running',
    });
  });

  it('settles the call on task_notification with the report as its result', () => {
    const view = at(5);
    const agent = calls(view)[0] as ToolCall;
    expect(agent.status).toBe('done');
    expect(agent.result).toMatchObject({ text: summary, isError: false, internal: false, ts: 5000 });
    expect(agent.task).toMatchObject({ status: 'completed', summary, completedAt: 5000 });
    expect(agent.durationMs).toBe(35_000); // the task's own duration, not the call's launch span
    const bar = deriveTimeline(view, 99_000)[0];
    expect(bar?.end).toBe(5000); // a settled call ends at its result, whatever `now` is
    const s = summarizeRun(view);
    expect(s.callCount).toBe(1);
    expect(s.subagentCount).toBe(1);
    expect(s.errorCount).toBe(0);
  });

  it('marks the call errored when the task ends any other way', () => {
    const killed = { ...stream[4], status: 'killed', summary: undefined } as Record<string, unknown>;
    const view = applyEvent(at(4), {
      laneId: 'lane-fixture',
      runId: 'run-async',
      seq: 5,
      receivedAt: 5000,
      event: killed as Envelope['event'],
    });
    const agent = calls(view)[0] as ToolCall;
    expect(agent.status).toBe('error');
    expect(agent.task?.status).toBe('killed');
    // no summary arrived, so the launch receipt's text stays as the only thing to show
    expect(agent.result).toMatchObject({ isError: true, text: launchText });
    expect(summarizeRun(view).errorCount).toBe(1);
  });

  it('settles the call on a terminal task_updated patch as well', () => {
    const updated = {
      type: 'system',
      subtype: 'task_updated',
      task_id: 'task-1',
      patch: { status: 'completed', end_time: 1_789_576_265_641 },
    };
    const view = applyEvent(at(4), {
      laneId: 'lane-fixture',
      runId: 'run-async',
      seq: 5,
      receivedAt: 5000,
      event: updated as Envelope['event'],
    });
    const agent = calls(view)[0] as ToolCall;
    expect(agent.status).toBe('done');
    expect(agent.task).toMatchObject({ status: 'completed', completedAt: 5000 });
  });
});

describe('interrupted runs', () => {
  const base = fresh('cancel');
  const env = (event: Record<string, unknown>, seq: number): Envelope => ({
    laneId: 'lane-fixture',
    runId: 'run-cancel',
    seq,
    receivedAt: 1000 + seq,
    event: event as Envelope['event'],
  });
  // What the CLI actually writes when our Stop lands as a SIGINT (QA F4).
  const interrupt = {
    type: 'user',
    uuid: 'u-int',
    parent_tool_use_id: null,
    message: { role: 'user', content: [{ type: 'text', text: '[Request interrupted by user]' }] },
  };
  const ede = {
    type: 'result',
    subtype: 'error_during_execution',
    is_error: true,
    duration_ms: 24_000,
    duration_api_ms: 20_000,
    num_turns: 15,
    total_cost_usd: 0.24,
    session_id: 'sess-1',
    errors: ['[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'],
  };

  it('renders the interrupt marker as a quiet notice, not a "User message" fold', () => {
    const v = applyEvent(base, env(interrupt, 1));
    expect(v.blocks).toHaveLength(1);
    expect(v.blocks[0]).toMatchObject({
      kind: 'notice',
      level: 'info',
      variant: 'interrupted',
      title: 'Stopped by you',
      id: 'interrupt:u-int',
    });
    expect(v.blocks[0]).not.toHaveProperty('text');
  });

  it('never surfaces an [ede_diagnostic] string, and reports no error at all after an interrupt', () => {
    const v = applyEvent(applyEvent(base, env(interrupt, 1)), env(ede, 2));
    expect(v.error).toBeUndefined();
    expect(v.numbers).toMatchObject({ numTurns: 15, costUsd: 0.24 });
    expect(JSON.stringify(v.blocks)).not.toContain('ede_diagnostic');
  });

  it('still reports the subtype when the same result arrives without an interrupt', () => {
    const v = applyEvent(base, env(ede, 1));
    expect(v.outcome).toEqual({ subtype: 'error_during_execution', isError: true });
    expect(v.error?.message).toBe('Run ended with error_during_execution');
  });

  it('keeps a real error message and drops only the diagnostic', () => {
    const v = applyEvent(base, env({ ...ede, errors: ['out of credit', ede.errors[0]] }, 1));
    expect(v.error?.message).toBe('out of credit');
  });

  it('a cancelled lifecycle clears the error the interrupted result left behind', () => {
    const v = applyEvent(base, env(ede, 1));
    expect(v.error).toBeDefined();
    const c = applyLifecycle(v, {
      laneId: v.laneId,
      runId: v.runId,
      origin: 'execution',
      status: 'cancelled',
      startedAt: v.startedAt,
      endedAt: v.startedAt + 5,
      error: { message: 'the agent reported an error result' },
    });
    expect(c.status).toBe('cancelled');
    expect(c.error).toBeUndefined();
  });
});

describe('run status belongs to the server lifecycle', () => {
  it('a result event fills numbers and outcome but does not end the run', () => {
    const v = reduce('flat');
    expect(v.numbers?.numTurns).toBeGreaterThan(0);
    expect(v.outcome).toEqual({ subtype: 'success', isError: false });
    expect(v.status).toBe('running');
    expect(v.endedAt).toBeUndefined();
  });

  it('an interrupted result never flips a cancelled run to failed (reload after Stop, QA 2026-09-19)', () => {
    // Hydration applies the row's lifecycle first and then folds the stored events, the interrupted
    // turn's `error_during_execution` result among them. The row is the truth.
    const base = fresh('hydrated');
    const cancelled = applyLifecycle(base, {
      laneId: base.laneId,
      runId: base.runId,
      origin: 'execution',
      status: 'cancelled',
      startedAt: base.startedAt,
      endedAt: base.startedAt + 14_000,
    });
    const v = applyEvent(cancelled, {
      laneId: base.laneId,
      runId: base.runId,
      seq: 1,
      receivedAt: base.startedAt + 13_000,
      event: {
        type: 'result',
        subtype: 'error_during_execution',
        is_error: true,
        num_turns: 10,
        total_cost_usd: 0.16,
        duration_ms: 14_000,
      } as Envelope['event'],
    });
    expect(v.status).toBe('cancelled');
    expect(v.endedAt).toBe(base.startedAt + 14_000);
    expect(v.numbers?.numTurns).toBe(10);
    expect(v.outcome?.isError).toBe(true);
  });
});

describe('applyLifecycle', () => {
  it('marks cancelled and keeps the trajectory', () => {
    const v = reduce('flat');
    const c = applyLifecycle(
      { ...v, status: 'running' },
      {
        laneId: v.laneId,
        runId: v.runId,
        origin: 'execution',
        status: 'cancelled',
        startedAt: v.startedAt,
        endedAt: v.startedAt + 5,
      },
    );
    expect(c.status).toBe('cancelled');
    expect(c.endedAt).toBe(v.startedAt + 5);
    expect(Object.keys(c.callsById)).toHaveLength(10);
  });
  it('never downgrades a finished run to running', () => {
    const folded = reduce('flat');
    const v = applyLifecycle(folded, {
      laneId: folded.laneId,
      runId: folded.runId,
      origin: 'execution',
      status: 'finished',
      startedAt: folded.startedAt,
      endedAt: folded.startedAt + 10,
    });
    expect(v.status).toBe('finished');
    const r = applyLifecycle(v, {
      laneId: v.laneId,
      runId: v.runId,
      origin: 'execution',
      status: 'running',
      startedAt: v.startedAt,
    });
    expect(r.status).toBe('finished');
  });
  it('applies a server-side failure with exit details', () => {
    const v = fresh('f');
    const f = applyLifecycle(v, {
      laneId: v.laneId,
      runId: v.runId,
      origin: 'execution',
      status: 'failed',
      startedAt: v.startedAt,
      endedAt: v.startedAt + 1,
      error: { message: '/bad/path is not a directory', exitCode: null },
    });
    expect(f.status).toBe('failed');
    expect(f.error?.message).toContain('not a directory');
  });
});
