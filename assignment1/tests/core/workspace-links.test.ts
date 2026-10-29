import { describe, expect, it } from 'vitest';
import { resolveWorkspaceLink, workspaceDir, workspaceFileUrl } from '@/core/workspaceLinks';

const CWD = '/repos/cafe';

/** From the workspace root, which is where the parent's final response is written from. */
const root = { dir: '', cwd: CWD };

describe('resolveWorkspaceLink', () => {
  it('turns a handoff index’s relative link into the file it names', () => {
    // The case the whole thing exists for: `HANDOFF.md` linking to its own proposals. Rendered as a
    // plain relative URL this navigated the console to `/lanes/proposals/plumbing.md`.
    expect(resolveWorkspaceLink('proposals/plumbing.md', root)).toEqual({
      kind: 'workspace',
      path: 'proposals/plumbing.md',
    });
    expect(resolveWorkspaceLink('./designs/plan.svg', root)).toEqual({
      kind: 'workspace',
      path: 'designs/plan.svg',
    });
  });

  it('resolves against the document’s own directory, not the workspace root', () => {
    const inside = { dir: 'handoff', cwd: CWD };
    expect(resolveWorkspaceLink('plumbing.md', inside)).toEqual({
      kind: 'workspace',
      path: 'handoff/plumbing.md',
    });
    // A nested document linking to a sibling directory's file.
    expect(resolveWorkspaceLink('../designs/plan.svg', inside)).toEqual({
      kind: 'workspace',
      path: 'designs/plan.svg',
    });
    expect(resolveWorkspaceLink('../HANDOFF.md', { dir: 'a/b', cwd: CWD })).toEqual({
      kind: 'workspace',
      path: 'a/HANDOFF.md',
    });
  });

  it('recognises the absolute path a model usually writes for a file it just made', () => {
    expect(resolveWorkspaceLink(`${CWD}/HANDOFF.md`, root)).toEqual({
      kind: 'workspace',
      path: 'HANDOFF.md',
    });
    expect(resolveWorkspaceLink(`${CWD}/designs/plan.svg`, { dir: 'handoff', cwd: CWD })).toEqual({
      kind: 'workspace',
      path: 'designs/plan.svg',
    });
  });

  it('leaves the official sources the research cited exactly as they are', () => {
    for (const href of [
      'https://www.boston.gov/permits',
      'http://example.gov',
      '//cdn.example.com/x.png',
      'mailto:planning@example.com',
    ]) {
      expect(resolveWorkspaceLink(href, root)).toEqual({ kind: 'external', href });
    }
  });

  it('refuses anything that is not a file in this workspace', () => {
    // Climbing out, pointing somewhere else on the machine, or naming a local file by URL: none of
    // these get workspace powers, and none are rewritten into something that looks like one.
    expect(resolveWorkspaceLink('../../etc/passwd', root).kind).toBe('other');
    expect(resolveWorkspaceLink('/etc/passwd', root).kind).toBe('other');
    expect(resolveWorkspaceLink('/repos/other/file.md', root).kind).toBe('other');
    expect(resolveWorkspaceLink('file:///etc/passwd', root).kind).toBe('other');
    expect(resolveWorkspaceLink('#section', root).kind).toBe('other');
    expect(resolveWorkspaceLink('', root).kind).toBe('other');
    expect(resolveWorkspaceLink(undefined, root).kind).toBe('other');
    // A sibling directory that merely shares a prefix is not inside the workspace.
    expect(resolveWorkspaceLink('/repos/cafe-old/x.md', root).kind).toBe('other');
  });

  it('decodes a link the way markdown wrote it, and keeps a fragment separate from the name', () => {
    expect(resolveWorkspaceLink('notes/site%20survey.md', root)).toEqual({
      kind: 'workspace',
      path: 'notes/site survey.md',
    });
    expect(resolveWorkspaceLink('HANDOFF.md#plumbing', root)).toEqual({
      kind: 'workspace',
      path: 'HANDOFF.md',
      fragment: 'plumbing',
    });
    // A query string means nothing to a file, so it is not made part of the name.
    expect(resolveWorkspaceLink('HANDOFF.md?v=2', root)).toEqual({
      kind: 'workspace',
      path: 'HANDOFF.md',
    });
    // A malformed escape is not a path this can open, and is not passed through half-decoded.
    expect(resolveWorkspaceLink('100%.md', root).kind).toBe('other');
  });
});

describe('workspaceFileUrl', () => {
  it('encodes each segment exactly once, so a literal % or / in a name survives', () => {
    expect(workspaceFileUrl('lane1', 'designs/plan.svg')).toBe('/api/workspace/lane1/designs/plan.svg');
    // `%` must reach the server as `%25`; decoding it twice there is what turned `100%.txt` into an
    // error and `a%2Fb.txt` into a two-segment path.
    expect(workspaceFileUrl('lane1', '100%.txt')).toBe('/api/workspace/lane1/100%25.txt');
    expect(workspaceFileUrl('lane1', 'a%2Fb.txt')).toBe('/api/workspace/lane1/a%252Fb.txt');
    expect(workspaceFileUrl('lane1', 'site survey.md')).toBe('/api/workspace/lane1/site%20survey.md');
    expect(workspaceFileUrl('lane1', 'x.md', { download: true })).toBe(
      '/api/workspace/lane1/x.md?download=1',
    );
  });
  // Refresh's reach into an image or a framed document: the bytes come from the raw route, and a
  // browser only re-requests a subresource whose URL changed.
  it('carries a refresh version, and omits it before the first refresh', () => {
    expect(workspaceFileUrl('lane1', 'plan.svg', { version: 0 })).toBe('/api/workspace/lane1/plan.svg');
    expect(workspaceFileUrl('lane1', 'plan.svg', { version: 3 })).toBe('/api/workspace/lane1/plan.svg?v=3');
    expect(workspaceFileUrl('lane1', 'plan.svg', { download: true, version: 3 })).toBe(
      '/api/workspace/lane1/plan.svg?download=1&v=3',
    );
  });
});

describe('workspaceDir', () => {
  it('is the folder a document lives in, and empty at the root', () => {
    expect(workspaceDir('handoff/plumbing.md')).toBe('handoff');
    expect(workspaceDir('a/b/c.md')).toBe('a/b');
    expect(workspaceDir('HANDOFF.md')).toBe('');
  });
});
