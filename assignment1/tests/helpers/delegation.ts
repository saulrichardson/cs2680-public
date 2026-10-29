import { CHROME_TOOL_PREFIX, type Envelope, type RawEvent } from '@/core/types';

/**
 * A stream shaped like a real asynchronous delegation, built event by event.
 *
 * The committed fixtures record the *synchronous* delegate shape (CLI before 2.1.270): the `Agent`
 * call's result arrives at the end and is the outcome. The shape that matters now is the other one
 * — a launch receipt in milliseconds, a task lifecycle over minutes, a report at the end — and the
 * states between those events (`launching`, `working`, an outcome that never arrives) are precisely
 * what the agent graph has to get right. Driving that from a builder rather than a recording is
 * what lets a test stop half way through and assert on the state the operator would be looking at.
 *
 * The payload shapes follow `docs/reference/claude-stream-json-types.md`, which is written from the
 * 2.1.270 wire.
 */
export class DelegationStream {
  private seq = 0;
  private ts: number;
  readonly envelopes: Envelope[] = [];

  constructor(
    readonly runId = 'run-1',
    readonly laneId = 'lane-1',
    startTs = Date.parse('2026-09-20T12:00:00.000Z'),
  ) {
    this.ts = startTs;
  }

  private push(event: RawEvent): this {
    this.ts += 100;
    this.seq += 1;
    this.envelopes.push({
      laneId: this.laneId,
      runId: this.runId,
      seq: this.seq,
      receivedAt: this.ts,
      event,
    });
    return this;
  }

  /** `system/init`, optionally reporting the Chrome MCP server and its tools as loaded. */
  init(opts: { browserTools?: string[] } = {}): this {
    const browserTools = opts.browserTools ?? [];
    return this.push({
      type: 'system',
      subtype: 'init',
      session_id: 's-1',
      cwd: '/tmp/project',
      model: 'claude-opus-5',
      tools: ['Bash', 'Read', 'Agent', ...browserTools],
      mcp_servers: browserTools.length ? [{ name: 'claude-in-chrome', status: 'connected' }] : [],
      permissionMode: 'acceptEdits',
      claude_code_version: '2.1.270',
    });
  }

  text(text: string, parent: string | null = null): this {
    return this.push({
      type: 'assistant',
      parent_tool_use_id: parent,
      message: { id: `m${this.seq}`, content: [{ type: 'text', text }] },
    });
  }

  /** An ordinary tool call, on the main thread or inside a delegate. */
  call(id: string, name: string, input: Record<string, unknown>, parent: string | null = null): this {
    return this.push({
      type: 'assistant',
      parent_tool_use_id: parent,
      message: { id: `m${this.seq}`, content: [{ type: 'tool_use', id, name, input }] },
    });
  }

  result(id: string, text: string, parent: string | null = null, isError = false): this {
    return this.push({
      type: 'user',
      parent_tool_use_id: parent,
      message: {
        role: 'user',
        content: [{ type: 'tool_result', tool_use_id: id, content: text, is_error: isError }],
      },
      tool_use_result: { stdout: text },
    });
  }

  /**
   * A browser tool call with no answer yet — the state every browser action passes through.
   *
   * Its own builder because the interesting states are on either side of the answer: in flight is
   * not the same as failed, and only a stream stopped between the two can show that.
   */
  browserCallPending(id: string, tool: string, parent: string | null = null): this {
    return this.call(id, `${CHROME_TOOL_PREFIX}${tool}`, { url: 'https://example.gov/permits' }, parent);
  }

  /** A browser tool call and its answer, as the Chrome MCP server names them. */
  browserCall(id: string, tool: string, parent: string | null = null, isError = false): this {
    return this.call(
      id,
      `${CHROME_TOOL_PREFIX}${tool}`,
      { url: 'https://example.gov/permits' },
      parent,
    ).result(id, isError ? 'Browser extension is not connected' : '<html>permits</html>', parent, isError);
  }

  /** The `Agent` call itself, plus the launch receipt that answers it within milliseconds. */
  delegate(
    id: string,
    taskId: string,
    opts: { description: string; prompt: string; subagentType?: string; parent?: string | null },
  ): this {
    this.call(
      id,
      'Agent',
      { description: opts.description, prompt: opts.prompt, subagent_type: opts.subagentType ?? 'Explore' },
      opts.parent ?? null,
    );
    return this.push({
      type: 'user',
      parent_tool_use_id: null,
      message: {
        role: 'user',
        content: [
          {
            type: 'tool_result',
            tool_use_id: id,
            content: 'Async agent launched successfully. (This tool result is internal metadata…)',
          },
        ],
      },
      tool_use_result: {
        isAsync: true,
        status: 'async_launched',
        agentId: taskId,
        description: opts.description,
        prompt: opts.prompt,
      },
    });
  }

  taskStarted(
    id: string,
    taskId: string,
    opts: { description: string; subagentType?: string; spawnDepth?: number },
  ): this {
    return this.push({
      type: 'system',
      subtype: 'task_started',
      task_id: taskId,
      tool_use_id: id,
      description: opts.description,
      subagent_type: opts.subagentType ?? 'Explore',
      spawn_depth: opts.spawnDepth ?? 1,
      task_type: 'local_agent',
    });
  }

  taskProgress(
    id: string,
    taskId: string,
    activity: string,
    usage: { total_tokens: number; tool_uses: number; duration_ms: number },
    lastTool = 'Read',
  ): this {
    return this.push({
      type: 'system',
      subtype: 'task_progress',
      task_id: taskId,
      tool_use_id: id,
      description: activity,
      usage,
      last_tool_name: lastTool,
    });
  }

  taskNotification(
    id: string,
    taskId: string,
    status: 'completed' | 'failed' | 'stopped',
    summary: string,
  ): this {
    return this.push({
      type: 'system',
      subtype: 'task_notification',
      task_id: taskId,
      tool_use_id: id,
      status,
      summary,
      usage: { total_tokens: 41_000, tool_uses: 9, duration_ms: 62_000 },
    });
  }

  done(): this {
    return this.push({
      type: 'result',
      subtype: 'success',
      is_error: false,
      duration_ms: 90_000,
      num_turns: 8,
      total_cost_usd: 0.42,
      session_id: 's-1',
    });
  }
}
