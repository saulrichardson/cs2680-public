import { describe, expect, it } from 'vitest';
import { agentNode, agentPath, deriveAgentGraph } from '@/core/agents';
import { applyEnvelopes, applyLifecycle, createRunView } from '@/core/reducer';
import { callKey } from '@/core/ref';
import type { Envelope, RunView } from '@/core/types';
import { DelegationStream } from '../helpers/delegation';
import { loadFixture } from '../helpers/fixtures';

function view(envelopes: Envelope[], runId = 'run-1', laneId = 'lane-1'): RunView {
  const base = createRunView({ runId, laneId, prompt: 'p', cwd: '/tmp/project', startedAt: 0 });
  return applyEnvelopes(base, envelopes);
}

/** The recorded synchronous delegation: one `Explore` subagent surveying a repository. */
function forwardedFixture(runId = 'run-fixture'): RunView {
  return view(loadFixture('subagent-forward', { laneId: 'lane-1', runId }), runId);
}

describe('deriveAgentGraph', () => {
  it('is empty until the model delegates, so a flat session carries no diagram', () => {
    const graph = deriveAgentGraph([view(loadFixture('flat', { laneId: 'lane-1', runId: 'r' }), 'r')]);
    expect(graph.nodes).toHaveLength(0);
    expect(graph.rootKeys).toHaveLength(0);
    expect(graph.unresolvedCount).toBe(0);
  });

  it('describes a real recorded delegation from its own task events', () => {
    const run = forwardedFixture();
    const graph = deriveAgentGraph([run]);
    expect(graph.nodes).toHaveLength(1);
    const node = graph.nodes[0];
    if (!node) throw new Error('expected a node');
    expect(node.state).toBe('completed');
    expect(node.subagentType).toBe('general-purpose');
    expect(node.title).toBe('Survey repository structure');
    expect(node.depth).toBe(1);
    expect(node.parentKey).toBeNull();
    // The brief the Agent call carried, and the report the task wrote — both from the stream.
    expect(node.assignment).toContain('Survey the repository at');
    expect(node.report).toContain('Survey complete');
    expect(node.reportIsMarkdown).toBe(true);
    // The forwarded trajectory is the delegate's own, not the parent's.
    expect(node.blocks.length).toBeGreaterThan(0);
    expect(node.blocks.filter((b) => b.kind === 'tool')).toHaveLength(11);
    expect(node.unresolved).toBe(false);
  });

  it('reports only figures the task published — never the parent run’s cost or turns', () => {
    const node = deriveAgentGraph([forwardedFixture()]).nodes[0];
    if (!node) throw new Error('expected a node');
    // The run itself reported a cost and a turn count; neither may appear on a delegate, because
    // the CLI publishes neither per task and borrowing them would bill one child for the session.
    expect(Object.keys(node.reported).sort()).toEqual(['durationMs', 'toolUses', 'totalTokens']);
    expect(node.reported.toolUses).toBe(11);
    expect(node.reported.totalTokens).toBeGreaterThan(0);
  });

  it('gives a replay of a recording its own nodes, because the call ids repeat', () => {
    // Exactly the situation the pair identity exists for: the same recording played back into the
    // lane it came from. Both runs hold a call with the same CLI id.
    const original = forwardedFixture('run-original');
    const replay = forwardedFixture('run-replay');
    const graph = deriveAgentGraph([original, replay]);
    expect(graph.nodes).toHaveLength(2);
    const [a, b] = graph.nodes;
    if (!a || !b) throw new Error('expected two nodes');
    expect(a.callId).toBe(b.callId);
    expect(a.key).not.toBe(b.key);
    expect(a.key).toBe(callKey({ runId: 'run-original', callId: a.callId }));
    expect(agentNode(graph, a.key)?.runId).toBe('run-original');
    expect(agentNode(graph, b.key)?.runId).toBe('run-replay');
  });

  it('reveals a node only once its own events have arrived, which is what replay timing means', () => {
    const stream = new DelegationStream()
      .init()
      .text('Planning the research.')
      .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' })
      .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
      .delegate('toolu_b', 'task_b', { description: 'Accessibility', prompt: 'Read the standard' })
      .taskStarted('toolu_b', 'task_b', { description: 'Accessibility' })
      .done();
    // Folded one event at a time, the graph grows exactly as the events are reached — which is the
    // same code path a paced replay drives, so a replay cannot show all its nodes up front.
    const counts: number[] = [];
    let run = createRunView({ runId: 'run-1', laneId: 'lane-1', prompt: 'p', cwd: '/tmp', startedAt: 0 });
    for (const env of stream.envelopes) {
      run = applyEnvelopes(run, [env]);
      const graph = deriveAgentGraph([run]);
      counts.push(graph.nodes.length);
    }
    expect(counts[0]).toBe(0);
    expect(counts.at(-1)).toBe(2);
    // Strictly non-decreasing: a node never disappears part-way through a replay.
    expect(counts.every((n, i) => i === 0 || n >= (counts[i - 1] as number))).toBe(true);
    // Two separate arrivals, not one batch at the end.
    expect(counts.filter((n, i) => n > (counts[i - 1] ?? 0))).toEqual([1, 2]);
  });

  it('follows the async lifecycle: launching, then working, then the reported outcome', () => {
    const upTo = (build: (s: DelegationStream) => DelegationStream) =>
      deriveAgentGraph([view(build(new DelegationStream()).envelopes)]).nodes[0];

    // Asked for, nothing back yet — not even the launch receipt.
    const asked = upTo((s) =>
      s.init().call('toolu_a', 'Agent', { description: 'Zoning rules', prompt: 'Read the code' }),
    );
    expect(asked?.state).toBe('launching');

    const launched = upTo((s) =>
      s.init().delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' }),
    );
    // The receipt says `isAsync`, so the subagent is away and running. What it must *not* do is
    // settle the call: the receipt answered the tool in milliseconds, and treating that as the
    // outcome is the mistake the whole async path exists to prevent.
    expect(launched?.state).toBe('working');
    expect(launched?.report).toBeUndefined();

    const working = upTo((s) =>
      s
        .init()
        .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' })
        .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
        .taskProgress('toolu_a', 'task_a', 'Reading the zoning table', {
          total_tokens: 12_000,
          tool_uses: 3,
          duration_ms: 8_000,
        }),
    );
    expect(working?.state).toBe('working');
    expect(working?.activity).toBe('Reading the zoning table');
    expect(working?.reported).toEqual({ totalTokens: 12_000, toolUses: 3, durationMs: 8_000 });

    const finished = upTo((s) =>
      s
        .init()
        .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' })
        .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
        .taskProgress('toolu_a', 'task_a', 'Reading the zoning table', {
          total_tokens: 12_000,
          tool_uses: 3,
          duration_ms: 8_000,
        })
        .taskNotification('toolu_a', 'task_a', 'completed', '## Findings\n\nSetback is 3 m.'),
    );
    expect(finished?.state).toBe('completed');
    expect(finished?.report).toContain('Setback is 3 m');
    // The progress line described what it was doing *then*; once it has stopped, showing it would
    // claim a delegate is still at work.
    expect(finished?.activity).toBeUndefined();

    const failed = upTo((s) =>
      s
        .init()
        .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' })
        .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
        .taskNotification('toolu_a', 'task_a', 'failed', 'Could not reach the registry.'),
    );
    expect(failed?.state).toBe('failed');
    expect(failed?.unresolved).toBe(false);
  });

  it('keeps the last reported state and flags the outcome unknown when the run is stopped first', () => {
    const stream = new DelegationStream()
      .init()
      .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the code' })
      .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
      .taskProgress('toolu_a', 'task_a', 'Reading the zoning table', {
        total_tokens: 9_000,
        tool_uses: 2,
        duration_ms: 5_000,
      });
    const cancelled = applyLifecycle(view(stream.envelopes), {
      laneId: 'lane-1',
      runId: 'run-1',
      status: 'cancelled',
      origin: 'execution',
      startedAt: 0,
      endedAt: 1,
    });
    const graph = deriveAgentGraph([cancelled]);
    const node = graph.nodes[0];
    if (!node) throw new Error('expected a node');
    // The evidence is that it was working; what it must not do is go on claiming that forever, or
    // be promoted to completed because the parent stopped.
    expect(node.state).toBe('working');
    expect(node.unresolved).toBe(true);
    expect(node.report).toBeUndefined();
    expect(graph.unresolvedCount).toBe(1);
    expect(graph.tally.completed).toBe(0);
    // The number the session chip shows. Summing the non-terminal tallies said "1 active" about a
    // session where nothing had been running since it was cancelled.
    expect(graph.activeCount).toBe(0);
    // The stale "currently doing" line is gone, but where it got to is not thrown away.
    expect(node.activity).toBeUndefined();
    expect(node.lastActivity).toBe('Reading the zoning table');
  });

  it('counts as active only the delegates whose run is still going', () => {
    const build = (b: (s: DelegationStream) => DelegationStream) =>
      b(
        new DelegationStream()
          .init()
          .delegate('toolu_a', 'task_a', { description: 'Zoning', prompt: 'p' })
          .taskStarted('toolu_a', 'task_a', { description: 'Zoning' })
          .delegate('toolu_b', 'task_b', { description: 'Fire code', prompt: 'p' })
          .taskStarted('toolu_b', 'task_b', { description: 'Fire code' })
          .taskNotification('toolu_b', 'task_b', 'completed', 'Two exits.'),
      ).envelopes;

    const live = deriveAgentGraph([view(build((s) => s))]);
    expect(live.activeCount).toBe(1);
    expect(live.tally.completed).toBe(1);
    expect(live.unresolvedCount).toBe(0);

    const stopped = deriveAgentGraph([
      applyLifecycle(view(build((s) => s)), {
        laneId: 'lane-1',
        runId: 'run-1',
        status: 'cancelled',
        origin: 'execution',
        startedAt: 0,
        endedAt: 1,
      }),
    ]);
    // The completed one stays completed — a native terminal event is authoritative — and the other
    // stops being counted as at work without being promoted to finished.
    expect(stopped.tally.completed).toBe(1);
    expect(stopped.activeCount).toBe(0);
    expect(stopped.unresolvedCount).toBe(1);
  });

  it('records each node’s provenance, so a replay’s delegates are distinguishable on screen', () => {
    // Same recording, same call ids, same titles: unique keys make them distinct in the DOM, and
    // this is what lets the view say which is the live run and which is playback.
    const live = forwardedFixture('run-live');
    const replayed = { ...forwardedFixture('run-replay'), origin: 'replay' as const };
    const graph = deriveAgentGraph([live, replayed]);
    expect(graph.nodes.map((n) => n.origin)).toEqual(['execution', 'replay']);
    expect(graph.nodes[0]?.title).toBe(graph.nodes[1]?.title);
    expect(new Set(graph.nodes.map((n) => n.runId)).size).toBe(2);
  });

  it('nests a delegate that delegates, and reads the path back to the session', () => {
    const stream = new DelegationStream()
      .init()
      .delegate('toolu_parent', 'task_p', { description: 'Survey the site', prompt: 'Look around' })
      .taskStarted('toolu_parent', 'task_p', { description: 'Survey the site' })
      // The nested Agent call arrives with the outer delegate as its parent, like every other
      // event the subagent produces.
      .delegate('toolu_child', 'task_c', {
        description: 'Check the fire code',
        prompt: 'Read NFPA',
        parent: 'toolu_parent',
      })
      .taskStarted('toolu_child', 'task_c', { description: 'Check the fire code', spawnDepth: 2 })
      .taskNotification('toolu_child', 'task_c', 'completed', 'Two exits required.')
      .taskNotification('toolu_parent', 'task_p', 'completed', 'Site surveyed.')
      .done();
    const graph = deriveAgentGraph([view(stream.envelopes)]);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.rootKeys).toHaveLength(1);
    const parent = agentNode(graph, graph.rootKeys[0]);
    if (!parent) throw new Error('expected a root');
    expect(parent.childKeys).toHaveLength(1);
    const child = agentNode(graph, parent.childKeys[0]);
    if (!child) throw new Error('expected a child');
    expect(child.depth).toBe(2);
    expect(child.parentKey).toBe(parent.key);
    expect(agentPath(graph, child.key).map((n) => n.title)).toEqual([
      'Survey the site',
      'Check the fire code',
    ]);
  });

  it('treats follow-up runs in one session as more roots of the same graph', () => {
    const first = view(
      new DelegationStream('run-1')
        .init()
        .delegate('toolu_a', 'task_a', { description: 'First', prompt: 'p' })
        .taskNotification('toolu_a', 'task_a', 'completed', 'done')
        .done().envelopes,
      'run-1',
    );
    const second = view(
      new DelegationStream('run-2')
        .init()
        .delegate('toolu_b', 'task_b', { description: 'Second', prompt: 'p' })
        .taskStarted('toolu_b', 'task_b', { description: 'Second' }).envelopes,
      'run-2',
    );
    const graph = deriveAgentGraph([first, second]);
    expect(graph.rootKeys).toHaveLength(2);
    expect(graph.nodes.map((n) => n.runId)).toEqual(['run-1', 'run-2']);
    expect(graph.tally).toMatchObject({ completed: 1, working: 1 });
  });

  it('exposes brief, figures and report even when nothing was forwarded', () => {
    // `--forward-subagent-text` off: the delegate's own steps never reach the stream. The panel
    // still has to be worth opening.
    const stream = new DelegationStream()
      .init()
      .delegate('toolu_a', 'task_a', { description: 'Zoning rules', prompt: 'Read the municipal code' })
      .taskStarted('toolu_a', 'task_a', { description: 'Zoning rules' })
      .taskNotification('toolu_a', 'task_a', 'completed', 'Setback is 3 m.')
      .done();
    const node = deriveAgentGraph([view(stream.envelopes)]).nodes[0];
    if (!node) throw new Error('expected a node');
    expect(node.blocks).toHaveLength(0);
    expect(node.assignment).toBe('Read the municipal code');
    expect(node.report).toBe('Setback is 3 m.');
    expect(node.reported.toolUses).toBe(9);
  });

  it('counts a delegate’s own browser tool calls, and not its siblings’', () => {
    const stream = new DelegationStream()
      .init({ browserTools: ['mcp__claude-in-chrome__navigate'] })
      .delegate('toolu_a', 'task_a', { description: 'Permits', prompt: 'p' })
      .taskStarted('toolu_a', 'task_a', { description: 'Permits' })
      .browserCall('b1', 'navigate', 'toolu_a')
      .browserCall('b2', 'read_page', 'toolu_a')
      .call('r1', 'Read', { file_path: '/tmp/x' }, 'toolu_a')
      .result('r1', 'contents', 'toolu_a')
      .taskNotification('toolu_a', 'task_a', 'completed', 'Found it.')
      .delegate('toolu_b', 'task_b', { description: 'Fire code', prompt: 'p' })
      .taskStarted('toolu_b', 'task_b', { description: 'Fire code' })
      .browserCall('b3', 'navigate', 'toolu_b')
      .done();
    const graph = deriveAgentGraph([view(stream.envelopes)]);
    expect(graph.nodes.map((n) => n.browserCalls)).toEqual([2, 1]);
    expect(graph.browserCalls).toBe(3);
  });

  it('refuses to read a node key off Object.prototype', () => {
    const graph = deriveAgentGraph([forwardedFixture()]);
    expect(agentNode(graph, 'constructor')).toBeUndefined();
    expect(agentNode(graph, '__proto__')).toBeUndefined();
    expect(agentNode(graph, null)).toBeUndefined();
    expect(agentPath(graph, 'nope')).toEqual([]);
  });
});
