// Writes fixtures/synth-2000.jsonl: a synthetic stream shaped like the real one, ~2,200 events.
//
// synthEnvelopes({ calls: 450 }) (the brief's original number) only yields 1,667 events at
// ~3.7 events/call with subagentEvery: 10 — short of "2,000". calls: 600 yields 2,222 events
// (~801 KB) for manually exercising a long trajectory.
//
// Run with `node --import tsx scripts/synth-fixture.mjs` (see the "fixtures:synth" package
// script) rather than `register('tsx/esm')`, which this tsx version does not expose.
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const { synthEnvelopes } = await import(path.join(root, 'tests/helpers/synth.ts'));

const envs = synthEnvelopes({ calls: 600, subagentEvery: 10 });
const outPath = path.join(root, 'fixtures', 'synth-2000.jsonl');
writeFileSync(outPath, `${envs.map((e) => JSON.stringify(e.event)).join('\n')}\n`);
console.log(`wrote ${envs.length} events to ${path.relative(root, outPath)}`);
