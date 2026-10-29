import { closeSync, ftruncateSync, mkdirSync, openSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { GET } from '@/app/api/workspace/[laneId]/[...path]/route';
import { createServerContext, registerServerContext, type ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { MAX_SERVE_BYTES } from '@/server/workspace/files';
import { makeTmpDir } from '../helpers/tmp';

/**
 * The raw file route, exercised the way Next actually calls it.
 *
 * The parameter contract is the point of most of this. App Router hands a catch-all route its
 * segments **already percent-decoded**, so the array holds the real file name. Decoding again broke
 * two perfectly ordinary names in opposite directions — `100%.txt` threw a `URIError` and was
 * rejected, `a%2Fb.txt` became a two-segment path — and neither failure is visible without driving
 * the handler with the parameters Next would supply.
 */
let live: ServerContext | null = null;
const cleanups: (() => void)[] = [];

afterEach(async () => {
  await live?.close();
  live = null;
  for (const c of cleanups.splice(0)) c();
});

/** A registered context with one lane whose workspace is a temp directory. */
function lane(): { laneId: string; root: string } {
  const data = makeTmpDir('mc-data-');
  const root = makeTmpDir('mc-ws-');
  cleanups.push(data.cleanup, root.cleanup);
  const ctx = createServerContext({
    host: '127.0.0.1',
    port: 0,
    dataDir: data.path,
    dbFile: ':memory:',
    claudeBin: '/bin/false',
    scratchRoot: join(data.path, 'scratch'),
  });
  live = ctx;
  registerServerContext(ctx);
  repo.createLane(ctx.db, {
    id: 'lane-1',
    name: 'Outputs',
    cwd: root.path,
    permission: 'allowlist',
    createdAt: 1,
  });
  return { laneId: 'lane-1', root: root.path };
}

/** As Next calls it: segments already decoded, and a plain `Request`. */
function get(laneId: string, path: string[], query = '') {
  return GET(new Request(`http://127.0.0.1/api/workspace/${laneId}/x${query}`), {
    params: Promise.resolve({ laneId, path }),
  });
}

describe('GET /api/workspace/[laneId]/[...path]', () => {
  it('serves a file, with the length it measured', async () => {
    const { laneId, root } = lane();
    writeFileSync(join(root, 'plan.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
    const res = await get(laneId, ['plan.svg']);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('image/svg+xml');
    const body = await res.arrayBuffer();
    expect(body.byteLength).toBe(Number(res.headers.get('content-length')));
    // The interactive-document policy, delivered by the same route the frame loads.
    const csp = res.headers.get('content-security-policy') ?? '';
    expect(csp).toMatch(/sandbox allow-scripts/);
    expect(csp).not.toMatch(/allow-same-origin/);
  });

  it('preserves a file name containing a literal percent sign', async () => {
    // The name a decode-twice bug rejected outright: `decodeURIComponent('100%.txt')` throws.
    const { laneId, root } = lane();
    writeFileSync(join(root, '100%.txt'), 'ninety nine point nine\n');
    const res = await get(laneId, ['100%.txt']);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('ninety nine point nine\n');
  });

  it('preserves a file name containing a literal %2F rather than splitting it into a path', async () => {
    const { laneId, root } = lane();
    writeFileSync(join(root, 'a%2Fb.txt'), 'one file, odd name\n');
    const res = await get(laneId, ['a%2Fb.txt']);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('one file, odd name\n');
  });

  it('serves a nested file, and a name with a space', async () => {
    const { laneId, root } = lane();
    mkdirSync(join(root, 'proposals'));
    writeFileSync(join(root, 'proposals', 'site survey.md'), '# Survey\n');
    // Two segments, the second holding a space: exactly what a handoff link resolves to.
    const res = await get(laneId, ['proposals', 'site survey.md']);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/markdown; charset=utf-8');
    expect(await res.text()).toBe('# Survey\n');
  });

  it('refuses to leave the workspace, whatever the segments say', async () => {
    const { laneId } = lane();
    for (const path of [['..', '..', 'etc', 'hosts'], ['..'], ['/etc/hosts']]) {
      const res = await get(laneId, path);
      expect([403, 404]).toContain(res.status);
    }
  });

  it('answers 404 for a lane it does not have and a file that is not there', async () => {
    const { laneId } = lane();
    expect((await get('nope', ['x.md'])).status).toBe(404);
    expect((await get(laneId, ['missing.md'])).status).toBe(404);
  });

  it('offers an unrenderable file as a download, never as something to interpret', async () => {
    const { laneId, root } = lane();
    writeFileSync(join(root, 'notes.bin'), Buffer.from([0, 1, 2, 3]));
    const res = await get(laneId, ['notes.bin'], '?download=1');
    expect(res.headers.get('content-type')).toBe('application/octet-stream');
    expect(res.headers.get('content-disposition')).toContain('attachment');
    expect(res.headers.get('content-security-policy')).not.toMatch(/allow-scripts/);
  });

  it('refuses a file larger than it will serve, without reading it', async () => {
    const { laneId, root } = lane();
    const big = join(root, 'huge.log');
    const fd = openSync(big, 'w');
    // Sparse: no disk cost, and past both the serve limit and Node's maximum buffer length, so this
    // can only pass if the size is checked before anything is read.
    ftruncateSync(fd, MAX_SERVE_BYTES + 5 * 1024 * 1024 * 1024);
    closeSync(fd);
    const res = await get(laneId, ['huge.log']);
    expect(res.status).toBe(413);
    expect(await res.text()).toMatch(/larger than this console will serve/);
  });

  it('sends an empty file as an empty body with a zero length', async () => {
    const { laneId, root } = lane();
    writeFileSync(join(root, 'empty.md'), '');
    const res = await get(laneId, ['empty.md']);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-length')).toBe('0');
    expect(await res.text()).toBe('');
  });
});
