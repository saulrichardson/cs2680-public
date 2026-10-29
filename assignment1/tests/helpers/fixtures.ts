import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseLine } from '@/core/schemas';
import type { Envelope, RawEvent } from '@/core/types';

export type FixtureName = 'flat' | 'subagent' | 'failed' | 'flat-allowed' | 'subagent-forward';

function fixturePath(name: FixtureName): string {
  return fileURLToPath(new URL(`../../fixtures/${name}.jsonl`, import.meta.url));
}

export function readFixtureLines(name: FixtureName): string[] {
  return readFileSync(fixturePath(name), 'utf8')
    .split('\n')
    .filter((l) => l.trim().length > 0);
}

/** Wrap fixture lines as envelopes the way the server would: seq increments, receivedAt follows timestamps. */
export function loadFixture(
  name: FixtureName,
  ids = { laneId: 'lane-fixture', runId: `run-${name}` },
): Envelope[] {
  let lastTs = Date.parse('2026-09-16T16:00:00.000Z');
  return readFixtureLines(name).map((line, i) => {
    const parsed = parseLine(line);
    const event: RawEvent = parsed.ok
      ? parsed.event
      : { type: 'unparsed', raw: parsed.raw, error: parsed.error };
    const ts = typeof event.timestamp === 'string' ? Date.parse(event.timestamp) : NaN;
    if (!Number.isNaN(ts)) lastTs = ts;
    return { laneId: ids.laneId, runId: ids.runId, seq: i + 1, receivedAt: lastTs, event };
  });
}
