import { describe, expect, it } from 'vitest';
import { applyEnvelopes, createRunView } from '@/core/reducer';
import { callElementId, callKey, callPath, flattenRows, sameCall } from '@/ui/store/rows';
import { loadFixture } from '../helpers/fixtures';

describe('flattenRows', () => {
  it('emits prompt, one row per main-thread block, and footer per run, in run order', () => {
    const a = applyEnvelopes(
      createRunView({ runId: 'a', laneId: 'l', prompt: 'first', cwd: '/', startedAt: 1 }),
      loadFixture('flat', { laneId: 'l', runId: 'a' }),
    );
    const b = createRunView({ runId: 'b', laneId: 'l', prompt: 'second', cwd: '/', startedAt: 2 });
    const rows = flattenRows([a, b]);
    expect(rows[0]).toMatchObject({ kind: 'prompt', runId: 'a' });
    expect(rows.filter((r) => r.runId === 'a' && r.kind === 'block')).toHaveLength(a.blocks.length);
    expect(rows.at(-3)).toMatchObject({ kind: 'footer', runId: 'a' });
    expect(rows.at(-2)).toMatchObject({ kind: 'prompt', runId: 'b' });
    expect(rows.at(-1)).toMatchObject({ kind: 'footer', runId: 'b' });
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });
});

describe('callPath', () => {
  const subagent = () =>
    applyEnvelopes(
      createRunView({ runId: 'a', laneId: 'l', prompt: 'survey', cwd: '/', startedAt: 1 }),
      loadFixture('subagent-forward', { laneId: 'l', runId: 'a' }),
    );

  it('returns a nested call with the ancestors whose groups have to unfold first', () => {
    const run = subagent();
    const parent = Object.values(run.callsById).find((c) => c.children.length > 0);
    const child = parent?.children.find((b) => b.kind === 'tool');
    expect(parent).toBeDefined();
    expect(child?.kind).toBe('tool');
    const childId = child?.kind === 'tool' ? child.callId : '';
    // The scroll target is the row `flattenRows` emits (the ancestor), and everything before the
    // last entry is a group to open.
    expect(callPath([run], { runId: 'a', callId: childId })).toEqual([parent?.id, childId]);
    const rows = flattenRows([run]);
    expect(
      rows.some((r) => r.kind === 'block' && r.block.kind === 'tool' && r.block.callId === parent?.id),
    ).toBe(true);
    // The nested call is deliberately not a row of its own: it lives inside its parent's group.
    expect(
      rows.some((r) => r.kind === 'block' && r.block.kind === 'tool' && r.block.callId === childId),
    ).toBe(false);
  });

  it('returns a top-level call as its own path, and null for a call no run holds', () => {
    const run = subagent();
    const topLevel = run.blocks.find((b) => b.kind === 'tool');
    const topId = topLevel?.kind === 'tool' ? topLevel.callId : '';
    expect(callPath([run], { runId: 'a', callId: topId })).toEqual([topId]);
    expect(callPath([run], { runId: 'a', callId: 'no-such-call' })).toBeNull();
    expect(callPath([], { runId: 'a', callId: topId })).toBeNull();
  });

  /**
   * R7. Replaying a recording into the lane it came from gives the lane two runs holding the very
   * same CLI call ids. A path asked for by call id alone answered from whichever run came first,
   * which is how a jump from the replay's outline scrolled to the original's card.
   */
  it('answers for the run that was asked about, not the first run holding that id', () => {
    const original = subagent();
    const replay = { ...subagent(), runId: 'replay-of-a' };
    const parent = Object.values(original.callsById).find((c) => c.children.length > 0);
    const child = parent?.children.find((b) => b.kind === 'tool');
    const childId = child?.kind === 'tool' ? child.callId : '';

    // the same id exists in both runs ...
    expect(replay.callsById[childId]).toBeDefined();
    // ... and each run answers for itself
    expect(callPath([original, replay], { runId: 'replay-of-a', callId: childId })).toEqual([
      parent?.id,
      childId,
    ]);
    expect(callPath([original, replay], { runId: 'a', callId: childId })).toEqual([parent?.id, childId]);
    // a run that does not exist has no path, even though the call id is in the lane
    expect(callPath([original, replay], { runId: 'nope', callId: childId })).toBeNull();

    // and the two cards are distinguishable everywhere a call is addressed
    const a = { runId: 'a', callId: childId };
    const b = { runId: 'replay-of-a', callId: childId };
    expect(callKey(a)).not.toBe(callKey(b));
    expect(callElementId(a)).not.toBe(callElementId(b));
    expect(sameCall(a, b)).toBe(false);
    expect(sameCall(a, { ...a })).toBe(true);
    expect(sameCall(a, null)).toBe(false);
    expect(sameCall(null, null)).toBe(false);
  });
});
