import { describe, expect, it } from 'vitest';
import { decodeProjectRoot, encodeProjectRoot, projectHref, projectLabel } from '@/ui/project';

/**
 * The encoding is shared by a client component and a server route, so it may not reach for a Node
 * global, and it has to survive the characters a real folder name actually contains.
 */
describe('project routes', () => {
  const paths = [
    '/Users/x/projects/notes',
    '/Users/x/my documents/site survey',
    '/tmp/100% done',
    '/tmp/café/naïve',
    '/tmp/a+b/c_d',
    '/tmp/trailing space ',
  ];

  it('round-trips folder paths through one opaque URL segment', () => {
    for (const path of paths) {
      const encoded = encodeProjectRoot(path);
      expect(encoded).not.toContain('/');
      expect(encoded).not.toContain('%');
      expect(encoded).not.toContain('=');
      expect(decodeProjectRoot(encoded)).toBe(path);
      expect(projectHref(path)).toBe(`/projects/${encoded}`);
    }
  });

  it('uses no Node-only globals, so a client navigation works too', () => {
    const buffer = (globalThis as Record<string, unknown>).Buffer;
    (globalThis as Record<string, unknown>).Buffer = undefined;
    try {
      expect(decodeProjectRoot(encodeProjectRoot('/tmp/café 100%'))).toBe('/tmp/café 100%');
    } finally {
      (globalThis as Record<string, unknown>).Buffer = buffer;
    }
  });

  it('labels a project by its own folder name', () => {
    expect(projectLabel('/Users/x/projects/notes')).toBe('notes');
    expect(projectLabel('/Users/x/projects/notes/')).toBe('notes');
    expect(projectLabel('/')).toBe('/');
  });
});
