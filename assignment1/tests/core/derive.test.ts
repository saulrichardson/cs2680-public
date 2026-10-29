import { describe, expect, it } from 'vitest';
import { deriveOutline, deriveTimeline, summarizeRun, timelineSpan } from '@/core/derive';
import { applyEnvelopes, applyLifecycle, createRunView } from '@/core/reducer';
import type { RawEvent } from '@/core/types';
import { loadFixture } from '../helpers/fixtures';

const cwd = '/tmp/example-project';
function view(name: 'flat' | 'subagent' | 'flat-allowed') {
  return applyEnvelopes(
    createRunView({ runId: name, laneId: 'l', prompt: 'p', cwd, startedAt: 0 }),
    loadFixture(name),
  );
}

describe('deriveOutline', () => {
  it('lists main-thread calls at depth 0 in order', () => {
    const items = deriveOutline(view('flat'), cwd);
    expect(items).toHaveLength(10);
    expect(items.every((i) => i.depth === 0)).toBe(true);
    expect(items[0]?.name).toBe('Skill');
    expect(items[1]?.name).toBe('Bash');
    expect(items[1]?.summary.length).toBeGreaterThan(0);
  });
  it('indents subagent calls under their Agent call', () => {
    const items = deriveOutline(view('subagent'), cwd);
    const agentIdx = items.findIndex((i) => i.name === 'Agent');
    expect(agentIdx).toBeGreaterThanOrEqual(0);
    const nested = items.slice(agentIdx + 1, agentIdx + 9);
    expect(nested).toHaveLength(8);
    expect(nested.every((i) => i.depth === 1 && i.name === 'Bash')).toBe(true);
    expect(items.filter((i) => i.depth === 0)).toHaveLength(1 + 3); // Agent + 3 main-thread Bash calls after it
  });
});

describe('malformed content blocks', () => {
  /**
   * R6, end to end through the real path an import takes: a line that is valid JSON and a valid
   * stream-json event, but whose `tool_use` block has no `input`. The reducer used to store the
   * block unnormalized, and `deriveOutline` — the first thing the lane renders — threw reading
   * `input.file_path`, taking the whole trajectory down.
   */
  it('renders a tool_use block that has no input at all', () => {
    const env = {
      laneId: 'l',
      runId: 'bad',
      seq: 1,
      receivedAt: 1000,
      event: {
        type: 'assistant',
        message: { id: 'm1', content: [{ type: 'tool_use', id: 'missing-input', name: 'Read' }] },
      },
    };
    const v = applyEnvelopes(
      createRunView({ runId: 'bad', laneId: 'l', prompt: 'p', cwd: '/tmp', startedAt: 1000 }),
      [env],
    );
    expect(v.callsById['missing-input']?.input).toEqual({});
    expect(() => deriveOutline(v)).not.toThrow();
    expect(deriveOutline(v).map((i) => i.name)).toEqual(['Read']);
    expect(() => deriveTimeline(v, 2000)).not.toThrow();
    expect(summarizeRun(v).callCount).toBe(1);
  });

  /**
   * A block type this console cannot render is nothing to report; a `tool_use` that is not a tool
   * use is a call the trajectory cannot show, and dropping it silently makes the run look like one
   * where the agent never made that call.
   */
  it('shows a malformed tool block and ignores an unsupported one', () => {
    const env = {
      laneId: 'l',
      runId: 'bad2',
      seq: 1,
      receivedAt: 1000,
      event: {
        type: 'assistant',
        message: {
          id: 'm1',
          content: [
            { type: 'tool_use', name: 'Read' },
            { type: 'image', source: {} },
          ],
        },
      },
    };
    const v = applyEnvelopes(
      createRunView({ runId: 'bad2', laneId: 'l', prompt: 'p', cwd: '/tmp', startedAt: 1000 }),
      [env],
    );
    // no half-built call ...
    expect(Object.keys(v.callsById)).toEqual([]);
    // ... but the malformed declaration is visible, with what actually arrived
    expect(v.blocks).toHaveLength(1);
    const block = v.blocks[0];
    expect(block?.kind).toBe('unparsed');
    if (block?.kind === 'unparsed') {
      expect(block.error).toMatch(/malformed tool_use block/);
      expect(block.raw).toContain('"name":"Read"');
    }
    expect(v.unparsedCount).toBe(1);
    // the image is simply not something this console renders
    expect(v.ignoredCount).toBe(1);
    expect(() => deriveOutline(v)).not.toThrow();
  });
});

/**
 * A recording is input, not a promise. These are the shapes an imported `events.jsonl` can have
 * that a hand-written fixture never does — and each of them used to take the whole trajectory down
 * on first render rather than showing a malformed run.
 */
describe('adversarial call graphs', () => {
  const view = (events: RawEvent[]) =>
    applyEnvelopes(
      createRunView({ runId: 'adv', laneId: 'l', prompt: 'p', cwd: '/tmp', startedAt: 0 }),
      events.map((event, i) => ({ laneId: 'l', runId: 'adv', seq: i + 1, receivedAt: i + 1, event })),
    );
  const read = (id: string) => ({ type: 'tool_use', id, name: 'Read', input: { file_path: 'a.md' } });
  const assistant = (parent: string | null, ...content: unknown[]): RawEvent => ({
    type: 'assistant',
    parent_tool_use_id: parent,
    message: { id: 'm', content },
  });

  /**
   * A tool call id naming an inherited property of `Object.prototype`. A bare `callsById[id]`
   * lookup returns a function, and the reducer read `.children` off it: "parent.children is not
   * iterable", for all three of `constructor`, `toString` and `__proto__`.
   */
  it.each(['constructor', 'toString', '__proto__', 'hasOwnProperty', 'valueOf'])(
    'does not mistake the inherited property %s for a tool call',
    (name) => {
      const v = view([assistant(name, read('t1'))]);
      expect(deriveOutline(v).map((i) => i.callId)).toEqual(['t1']);
      // the unknown parent puts the call on the main thread, where orphaned calls already go
      expect(v.callsById.t1?.parentToolUseId).toBe(name);
      expect(summarizeRun(v).callCount).toBe(1);

      // ... and a call that *is* named that way is a call like any other
      const named = view([assistant(null, read(name))]);
      expect(deriveOutline(named).map((i) => i.callId)).toEqual([name]);
      expect(Object.hasOwn(named.callsById, name)).toBe(true);
    },
  );

  /**
   * The same tool call id declared twice, the second time naming itself as its own parent. The
   * repeat replaced the original and appended a block inside its own children — a block containing
   * itself — and the first render blew the stack.
   */
  it('keeps the first declaration when an id is repeated, and cannot be made to recurse', () => {
    const agent = { type: 'tool_use', id: 'same', name: 'Agent', input: { description: 'first' } };
    const repeat = { type: 'tool_use', id: 'same', name: 'Bash', input: { command: 'second' } };
    const v = view([assistant(null, agent), assistant('same', repeat)]);

    // the call is the one that was declared first, unchanged
    expect(v.callsById.same).toMatchObject({ name: 'Agent', input: { description: 'first' } });
    expect(v.callsById.same?.parentToolUseId).toBeNull();
    // one call, one row for it, and the repeat recorded as what it was
    expect(deriveOutline(v).map((i) => i.callId)).toEqual(['same']);
    expect(summarizeRun(v).callCount).toBe(1);
    // the repeat named `same` as its parent, so the notice sits on that call's own thread
    const notices = [...v.blocks, ...(v.callsById.same?.children ?? [])].filter((b) => b.kind === 'notice');
    expect(notices).toHaveLength(1);
    const notice = notices[0];
    if (notice?.kind !== 'notice') throw new Error('expected a notice block');
    expect(notice.title).toMatch(/Repeated tool call id same/);
    expect(notice.text).toContain('Agent');
    expect(v.ignoredCount).toBe(1);
    expect(() => deriveTimeline(v, 10)).not.toThrow();
  });

  it('refuses a parent that would make a call its own ancestor', () => {
    // a → b → a: the second edge is the one that closes the loop
    const v = view([
      assistant(null, read('a')),
      assistant('a', read('b')),
      assistant('b', { type: 'tool_use', id: 'c', name: 'Read', input: {} }),
    ]);
    expect(v.callsById.c?.parentToolUseId).toBe('b');

    const looped = view([assistant(null, read('x')), assistant('x', read('x'))]);
    // the repeat is refused before ancestry is even considered, so `x` stays a top-level call
    expect(looped.callsById.x?.parentToolUseId).toBeNull();
    expect(() => deriveOutline(looped)).not.toThrow();
    expect(deriveOutline(looped)).toHaveLength(1);
  });

  it('survives a cycle assembled outside the reducer', () => {
    // `walkCalls` is not the place the invariant lives, but a recursive reader has to be total:
    // a view built some other way must not take the page down.
    const base = view([assistant(null, read('p')), assistant('p', read('q'))]);
    const p = base.callsById.p;
    const q = base.callsById.q;
    if (!p || !q) throw new Error('fixture did not build the nesting');
    const cyclic = {
      ...base,
      callsById: {
        ...base.callsById,
        q: { ...q, children: [{ kind: 'tool' as const, callId: 'p' }] },
      },
    };
    expect(() => deriveOutline(cyclic)).not.toThrow();
    expect(deriveOutline(cyclic).map((i) => i.callId)).toEqual(['p', 'q']);
    expect(() => summarizeRun(cyclic)).not.toThrow();
  });
});

describe('deriveTimeline', () => {
  it('produces one bar per call with start <= end and parent links', () => {
    const v = view('subagent');
    const bars = deriveTimeline(v, Date.now());
    expect(bars).toHaveLength(Object.keys(v.callsById).length);
    expect(bars.every((b) => b.start <= b.end)).toBe(true);
    const agent = bars.find((b) => b.name === 'Agent');
    expect(bars.filter((b) => b.parentCallId === agent?.callId)).toHaveLength(8);
    expect(
      agent &&
        bars
          .filter((b) => b.parentCallId === agent.callId)
          .every((b) => b.start >= agent.start && b.end <= agent.end),
    ).toBe(true);
  });
  /**
   * R8. A pending call used to run to the wall clock forever, so a cancelled run inspected ten
   * minutes later drew a bar ending ten minutes past the run's own end — 60,100% of the axis in the
   * reproduction. The run's authoritative `endedAt` is where an unanswered call stops: nothing can
   * have happened in that run after it. The call stays `pending`, so it is never mistaken for one
   * that succeeded.
   */
  it('stops a pending call at the run end once the run is terminal', () => {
    const v = view('flat');
    const pendingId = Object.keys(v.callsById)[0] as string;
    const call = v.callsById[pendingId] as NonNullable<(typeof v.callsById)[string]>;
    for (const status of ['cancelled', 'failed', 'finished'] as const) {
      const ended = applyLifecycle(
        {
          ...v,
          callsById: {
            ...v.callsById,
            [pendingId]: { ...call, status: 'pending' as const, result: undefined },
          },
        },
        {
          laneId: 'l',
          runId: v.runId,
          origin: 'execution',
          status,
          startedAt: v.startedAt,
          endedAt: call.ts + 2000,
        },
      );
      const bar = deriveTimeline(ended, call.ts + 600_000).find((b) => b.callId === pendingId);
      expect(bar?.end).toBe(call.ts + 2000);
      expect(bar?.status).toBe('pending');
    }
  });

  it('stops an unfinished async delegate at the run end when the run is cancelled', () => {
    // The delegate case is the one that actually happens: an Agent call is answered in milliseconds
    // with a launch receipt and stays pending until its task reports, so a Stop mid-delegation
    // always leaves one pending call behind.
    const v = view('subagent');
    const agent = Object.values(v.callsById).find((c) => c.name === 'Agent');
    if (!agent) throw new Error('the subagent fixture has no Agent call');
    const pendingAgent = {
      ...v,
      callsById: {
        ...v.callsById,
        [agent.id]: { ...agent, status: 'pending' as const, durationMs: undefined },
      },
    };
    const cancelled = applyLifecycle(pendingAgent, {
      laneId: 'l',
      runId: v.runId,
      origin: 'execution',
      status: 'cancelled',
      startedAt: v.startedAt,
      endedAt: agent.ts + 5000,
    });
    const bars = deriveTimeline(cancelled, agent.ts + 900_000);
    expect(bars.find((b) => b.callId === agent.id)?.end).toBe(agent.ts + 5000);
    // no *unresolved* call keeps accruing past the run's end; a resolved one keeps its recorded
    // result time, which is a fact about the recording rather than something to rewrite
    for (const b of bars.filter((b) => b.status === 'pending')) {
      expect(b.end).toBeLessThanOrEqual(agent.ts + 5000);
    }
  });

  /**
   * The axis a run's bars are drawn on. A replay re-bases event stamps on the new run's start while
   * `endedAt` is copied from the recording's own duration, so a recorded result can legitimately
   * land after the run's declared end. Widening the axis to contain the bars keeps every bar inside
   * 0–100% without rewriting when a tool call actually answered.
   */
  it('gives a span that contains every bar and never collapses to zero', () => {
    const v = view('flat');
    const bars = deriveTimeline(v, v.startedAt);
    const last = Math.max(...bars.map((b) => b.end));
    const early = applyLifecycle(v, {
      laneId: 'l',
      runId: v.runId,
      origin: 'execution',
      status: 'finished',
      startedAt: v.startedAt,
      endedAt: v.startedAt + 1,
    });
    const span = timelineSpan(early, bars, v.startedAt);
    expect(span.start).toBe(v.startedAt);
    expect(span.end).toBe(last);

    // a run with no bars and no end still has a usable, non-empty axis
    const empty = timelineSpan(
      createRunView({ runId: 'e', laneId: 'l', prompt: '', cwd: '/tmp', startedAt: 500 }),
      [],
      900,
    );
    expect(empty.start).toBe(500);
    expect(empty.end).toBe(900);
    expect(empty.end).toBeGreaterThan(empty.start);
  });

  it('still runs a pending call to now while the run is going', () => {
    const v = view('flat');
    const pendingId = Object.keys(v.callsById)[0] as string;
    const call = v.callsById[pendingId] as NonNullable<(typeof v.callsById)[string]>;
    const stillPending = {
      ...v,
      callsById: { ...v.callsById, [pendingId]: { ...call, status: 'pending' as const, result: undefined } },
    };
    const now = call.ts + 99_999;
    const bar = deriveTimeline(stillPending, now).find((b) => b.callId === pendingId);
    expect(bar?.end).toBe(now);
  });
});

describe('summarizeRun', () => {
  it('counts calls by class, files, and line deltas', () => {
    const s = summarizeRun(view('flat-allowed'));
    expect(s.callCount).toBe(13);
    expect(s.callsByClass.search).toBe(4);
    expect(s.callsByClass.mutate).toBe(2);
    expect(s.callsByClass.execute).toBe(6);
    expect(s.filesTouched.length).toBeGreaterThanOrEqual(1);
    expect(s.filesTouched.every((f) => f.endsWith('.py'))).toBe(true);
    expect(s.linesAdded).toBeGreaterThan(0);
    // flat-allowed contains one Bash call whose result is `is_error: true` (a
    // ModuleNotFoundError from `python3 -c ...`), verified via:
    //   grep -c '"is_error":true' fixtures/flat-allowed.jsonl  -> 1
    // so the reducer marks that call `status: 'error'` and summarizeRun counts it.
    expect(s.errorCount).toBe(1);
    expect(s.subagentCount).toBe(0);
  });
  it('counts subagents', () => {
    expect(summarizeRun(view('subagent')).subagentCount).toBe(1);
  });
});
