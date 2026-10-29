import { describe, expect, it } from 'vitest';
import {
  planWrapUp,
  quoteText,
  sourceOutcome,
  sourceRevision,
  WRAPUP_MAX_REPORT_CHARS,
  type WrapUpRequest,
  type WrapUpSourceFacts,
} from '@/core/wrapup';

function facts(over: Partial<WrapUpSourceFacts> & { laneId: string }): WrapUpSourceFacts {
  return {
    name: over.laneId,
    projectRoot: '/projects/cafe',
    revision: '1:r1:finished',
    outcome: 'finished',
    busy: false,
    ...over,
  };
}

function request(over: Partial<WrapUpRequest> = {}): WrapUpRequest {
  return {
    projectRoot: '/projects/cafe',
    sources: [{ laneId: 'a', revision: '1:r1:finished' }],
    files: [],
    acknowledgePartial: false,
    ...over,
  };
}

describe('sourceRevision', () => {
  it('changes when a run is added, and when the last run’s status changes', () => {
    const one = [{ id: 'r1', status: 'finished', startedAt: 1 }];
    expect(sourceRevision(one)).toBe('1:r1:finished');
    expect(sourceRevision([...one, { id: 'r2', status: 'running', startedAt: 2 }])).not.toBe(
      sourceRevision(one),
    );
    expect(sourceRevision([{ id: 'r1', status: 'failed', startedAt: 1 }])).not.toBe(sourceRevision(one));
    // A session that has never executed still has an answer, and it is stable.
    expect(sourceRevision([])).toBe('0:none:none');
  });
});

describe('sourceOutcome', () => {
  it('reports the latest execution, and never reads an unknown status as finished', () => {
    expect(sourceOutcome([])).toBe('none');
    expect(
      sourceOutcome([
        { id: 'r1', status: 'failed', startedAt: 1 },
        { id: 'r2', status: 'finished', startedAt: 2 },
      ]),
    ).toBe('finished');
    expect(sourceOutcome([{ id: 'r1', status: 'something-else', startedAt: 1 }])).toBe('failed');
  });
});

describe('planWrapUp', () => {
  it('accepts sessions of one project that have finished', () => {
    const decision = planWrapUp(
      request({
        sources: [
          { laneId: 'a', revision: '1:r1:finished' },
          { laneId: 'b', revision: '2:r9:finished' },
        ],
      }),
      [facts({ laneId: 'a' }), facts({ laneId: 'b', revision: '2:r9:finished' })],
    );
    expect(decision.ok).toBe(true);
    if (!decision.ok) return;
    expect(decision.plan.sources.map((s) => s.laneId)).toEqual(['a', 'b']);
    expect(decision.plan.partial).toBe(false);
  });

  it('refuses a session from another project, naming it', () => {
    const decision = planWrapUp(
      request({
        sources: [
          { laneId: 'a', revision: '1:r1:finished' },
          { laneId: 'b', revision: '1:r1:finished' },
        ],
      }),
      [facts({ laneId: 'a' }), facts({ laneId: 'b', name: 'Plumbing', projectRoot: '/projects/other' })],
    );
    expect(decision).toMatchObject({
      ok: false,
      refusal: { code: 'BAD_REQUEST', message: expect.stringContaining('Plumbing') },
    });
    if (decision.ok) return;
    expect(decision.refusal.message).toContain('another project');
  });

  it('refuses a session the server is still running, whether the row or the ownership says so', () => {
    for (const source of [facts({ laneId: 'a', busy: true }), facts({ laneId: 'a', outcome: 'running' })]) {
      const decision = planWrapUp(request(), [source]);
      expect(decision).toMatchObject({ ok: false, refusal: { code: 'PRECONDITION_FAILED' } });
      if (decision.ok) continue;
      expect(decision.refusal.message).toContain('still running');
    }
  });

  it('needs the operator to acknowledge a source that did not finish', () => {
    for (const outcome of ['failed', 'cancelled', 'none'] as const) {
      const refused = planWrapUp(request(), [facts({ laneId: 'a', name: 'Survey', outcome })]);
      expect(refused).toMatchObject({ ok: false, refusal: { code: 'PRECONDITION_FAILED' } });
      if (!refused.ok) expect(refused.refusal.message).toContain('Survey');
      // …and goes ahead once they have, recording that the package is partial.
      const allowed = planWrapUp(request({ acknowledgePartial: true }), [facts({ laneId: 'a', outcome })]);
      expect(allowed).toMatchObject({ ok: true, plan: { partial: true } });
    }
  });

  it('refuses a source that changed between being chosen and being confirmed', () => {
    const decision = planWrapUp(request({ sources: [{ laneId: 'a', revision: '1:r1:finished' }] }), [
      facts({ laneId: 'a', name: 'Cafe', revision: '2:r2:finished' }),
    ]);
    expect(decision).toMatchObject({
      ok: false,
      refusal: { code: 'CONFLICT', message: expect.stringContaining('Cafe') },
    });
  });

  it('refuses no sources, too many, duplicates, and files from a session that is not one', () => {
    expect(planWrapUp(request({ sources: [] }), [])).toMatchObject({ ok: false });
    expect(
      planWrapUp(
        request({
          sources: ['a', 'b', 'c', 'd'].map((laneId) => ({ laneId, revision: '1:r1:finished' })),
        }),
        ['a', 'b', 'c', 'd'].map((laneId) => facts({ laneId })),
      ),
    ).toMatchObject({ ok: false, refusal: { message: expect.stringContaining('at most 3') } });
    expect(
      planWrapUp(
        request({
          sources: [
            { laneId: 'a', revision: '1:r1:finished' },
            { laneId: 'a', revision: '1:r1:finished' },
          ],
        }),
        [facts({ laneId: 'a' })],
      ),
    ).toMatchObject({ ok: false, refusal: { message: expect.stringContaining('twice') } });
    expect(
      planWrapUp(request({ files: [{ laneId: 'z', path: 'notes.md' }] }), [facts({ laneId: 'a' })]),
    ).toMatchObject({ ok: false, refusal: { message: expect.stringContaining('not one of the sources') } });
  });
});

describe('quoteText', () => {
  it('keeps short text whole and records exactly what it dropped', () => {
    expect(quoteText('short', 1000)).toEqual({ text: 'short' });
    const long = 'x'.repeat(WRAPUP_MAX_REPORT_CHARS + 25);
    expect(quoteText(long, Number.MAX_SAFE_INTEGER)).toEqual({
      text: 'x'.repeat(WRAPUP_MAX_REPORT_CHARS),
      dropped: 25,
    });
    // A spent budget yields nothing with the whole length recorded, rather than a silent omission.
    expect(quoteText('abcdef', 0)).toEqual({ text: '', dropped: 6 });
    expect(quoteText('abcdef', 2)).toEqual({ text: 'ab', dropped: 4 });
  });
});
