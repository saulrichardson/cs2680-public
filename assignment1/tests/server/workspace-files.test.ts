import { execFileSync, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import { mkdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { previewSandboxFor } from '@/core/preview';
import {
  contentTypeOf,
  listWorkspace,
  MAX_TEXT_BYTES,
  previewHeaders,
  previewKind,
  previewSandbox,
  readBoundedWorkspaceFile,
  readWorkspaceFile,
  resolveInWorkspace,
  WorkspaceError,
} from '@/server/workspace/files';
import { makeTmpDir } from '../helpers/tmp';

/** A workspace shaped like one a cafe research session would leave behind, plus a way out of it. */
function workspace() {
  const root = makeTmpDir('mc-ws-');
  const outside = makeTmpDir('mc-secret-');
  writeFileSync(join(outside.path, 'secrets.txt'), 'do not read me\n');
  writeFileSync(join(root.path, 'BRIEF.md'), '# Brief\n\nFit-out for a 60 m² cafe.\n');
  writeFileSync(join(root.path, 'plan.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');
  writeFileSync(join(root.path, 'notes.bin'), Buffer.from([0, 1, 2, 3]));
  mkdirSync(join(root.path, 'handoff'));
  writeFileSync(join(root.path, 'handoff', 'plumbing.md'), '# Plumbing\n');
  mkdirSync(join(root.path, '.git'));
  writeFileSync(join(root.path, '.git', 'config'), 'nope\n');
  // Two symlinks: one that stays inside, one that escapes. The second is the interesting one.
  symlinkSync(join(root.path, 'handoff'), join(root.path, 'inside-link'));
  symlinkSync(outside.path, join(root.path, 'escape'));
  symlinkSync(join(outside.path, 'secrets.txt'), join(root.path, 'secrets-link.txt'));
  const cleanup = () => {
    root.cleanup();
    outside.cleanup();
  };
  return { root: root.path, outside: outside.path, cleanup };
}

describe('workspace file access', () => {
  it('lists the workspace, skipping .git and marking a link that leaves it', () => {
    const ws = workspace();
    try {
      const listing = listWorkspace(ws.root, '');
      const names = listing.entries.map((e) => e.name);
      expect(names).toContain('BRIEF.md');
      expect(names).toContain('handoff');
      // Skipped because listing it is useless, not because it is forbidden.
      expect(names).not.toContain('.git');
      // Directories first, so a workspace reads like a file list and not like a hash order.
      expect(listing.entries[0]?.kind).toBe('directory');
      const escapingDir = listing.entries.find((e) => e.name === 'escape');
      const secrets = listing.entries.find((e) => e.name === 'secrets-link.txt');
      // Shown — the operator should be able to see the agent made these — but never openable.
      expect(escapingDir?.blocked).toBe(true);
      expect(secrets?.blocked).toBe(true);
      // A link that stays inside is an ordinary entry.
      expect(listing.entries.find((e) => e.name === 'inside-link')?.blocked).toBeUndefined();
      expect(listing.entries.find((e) => e.name === 'inside-link')?.kind).toBe('directory');
    } finally {
      ws.cleanup();
    }
  });

  it('reads a markdown file inside the workspace', () => {
    const ws = workspace();
    try {
      const file = readWorkspaceFile(ws.root, 'handoff/plumbing.md');
      expect(file.preview).toBe('markdown');
      expect(file.text).toBe('# Plumbing\n');
      expect(file.truncated).toBe(false);
    } finally {
      ws.cleanup();
    }
  });

  it('refuses to walk out with ..', () => {
    const ws = workspace();
    try {
      expect(() => resolveInWorkspace(ws.root, '../')).toThrow(WorkspaceError);
      expect(() => readWorkspaceFile(ws.root, '../../etc/hosts')).toThrow(/outside|not in this workspace/i);
      // Including a traversal that climbs and comes back to a real file elsewhere.
      const escapeRel = `../${ws.outside.split('/').pop()}/secrets.txt`;
      expect(() => readWorkspaceFile(ws.root, escapeRel)).toThrow(WorkspaceError);
    } finally {
      ws.cleanup();
    }
  });

  it('refuses an absolute path even when it names a real file', () => {
    const ws = workspace();
    try {
      // `resolve(root, '/etc/passwd')` is `/etc/passwd`, so this has to be caught before resolution.
      expect(() => resolveInWorkspace(ws.root, '/etc/hosts')).toThrow(/inside the workspace/i);
      expect(() => resolveInWorkspace(ws.root, join(ws.outside, 'secrets.txt'))).toThrow(WorkspaceError);
    } finally {
      ws.cleanup();
    }
  });

  it('refuses a symlink that escapes the workspace, even though the path looks contained', () => {
    const ws = workspace();
    try {
      // This is the one `..`-rejection alone would miss: the request is a plain name inside the
      // workspace, and only resolving the link shows where it goes.
      expect(() => readWorkspaceFile(ws.root, 'secrets-link.txt')).toThrow(/outside/i);
      expect(() => listWorkspace(ws.root, 'escape')).toThrow(/outside/i);
      expect(() => readWorkspaceFile(ws.root, 'escape/secrets.txt')).toThrow(/outside/i);
    } finally {
      ws.cleanup();
    }
  });

  it('follows a symlink that stays inside', () => {
    const ws = workspace();
    try {
      expect(readWorkspaceFile(ws.root, 'inside-link/plumbing.md').text).toBe('# Plumbing\n');
    } finally {
      ws.cleanup();
    }
  });

  it('says a missing file is missing rather than failing obscurely', () => {
    const ws = workspace();
    try {
      expect(() => readWorkspaceFile(ws.root, 'nope.md')).toThrow(/not in this workspace/i);
      const err = (() => {
        try {
          readWorkspaceFile(ws.root, 'nope.md');
        } catch (e) {
          return e;
        }
      })();
      expect((err as WorkspaceError).kind).toBe('missing');
    } finally {
      ws.cleanup();
    }
  });

  it('truncates a very long text file and says so', () => {
    const ws = workspace();
    try {
      writeFileSync(join(ws.root, 'huge.md'), 'x'.repeat(MAX_TEXT_BYTES + 1000));
      const file = readWorkspaceFile(ws.root, 'huge.md');
      expect(file.truncated).toBe(true);
      expect(file.text?.length).toBe(MAX_TEXT_BYTES);
      // `size` still describes the file, not the slice, so the interface can offer the download.
      expect(file.size).toBe(MAX_TEXT_BYTES + 1000);
    } finally {
      ws.cleanup();
    }
  });

  it('reads only the bounded prefix, whatever the file’s size', () => {
    // The files this shows are ones an agent generated, which includes the enormous log a command
    // left behind — so the bound has to be at the read, not a slice taken afterwards.
    //
    // A 5 GB sparse file makes that provable rather than merely plausible: it costs no disk space,
    // and it is past Node's maximum buffer length, so `readFileSync` cannot succeed on it at all
    // (`ERR_FS_FILE_TOO_LARGE`). This test passing *is* the evidence that the whole file is never
    // read; the previous implementation would have thrown here.
    const ws = workspace();
    try {
      const big = join(ws.root, 'run.log');
      const fd = fs.openSync(big, 'w');
      fs.ftruncateSync(fd, 5 * 1024 * 1024 * 1024);
      fs.closeSync(fd);
      expect(() => fs.readFileSync(big)).toThrow(/greater than|too large/i);

      const file = readWorkspaceFile(ws.root, 'run.log');
      expect(file.truncated).toBe(true);
      expect(file.text?.length).toBe(MAX_TEXT_BYTES);
      expect(file.size).toBe(5 * 1024 * 1024 * 1024);
    } finally {
      ws.cleanup();
    }
  });

  it('reads a short file whole, with nothing marked truncated', () => {
    const ws = workspace();
    try {
      const file = readWorkspaceFile(ws.root, 'BRIEF.md');
      expect(file.text).toBe('# Brief\n\nFit-out for a 60 m² cafe.\n');
      expect(file.truncated).toBe(false);
    } finally {
      ws.cleanup();
    }
  });

  it('offers a preview only for kinds it can render, and octet-stream for the rest', () => {
    expect(previewKind('BRIEF.md')).toBe('markdown');
    expect(previewKind('plan.svg')).toBe('document');
    expect(previewKind('layout.html')).toBe('document');
    expect(previewKind('photo.png')).toBe('image');
    expect(previewKind('notes.bin')).toBe('none');
    expect(contentTypeOf('notes.bin')).toBe('application/octet-stream');
    expect(contentTypeOf('plan.svg')).toBe('image/svg+xml');
  });

  it('lets a generated document run its own scripts, in an origin it cannot escape', () => {
    // The cafe output is a self-contained viewer with layer toggles. A preview whose controls are
    // dead is a misleading preview, so scripts are allowed — and the origin is not.
    const headers = previewHeaders('layout.html', false);
    const csp = headers['content-security-policy'] ?? '';
    expect(csp).toMatch(/\bsandbox allow-scripts\b/);
    expect(csp).toMatch(/script-src 'unsafe-inline'/);
    // The one thing that must never appear: with it, the document would run *as* Mission Control
    // and could read its pages and drive its endpoints with the operator's session.
    expect(csp).not.toMatch(/allow-same-origin/);
    // Self-contained means self-contained: nothing remote, and no way to post data out.
    expect(csp).toMatch(/default-src 'none'/);
    expect(csp).not.toMatch(/connect-src/);
    expect(csp).toMatch(/form-action 'none'/);
    expect(csp).toMatch(/frame-ancestors 'self'/);
    // Nor any of the other capabilities a sandbox can grant.
    for (const grant of ['allow-forms', 'allow-popups', 'allow-top-navigation', 'allow-modals'])
      expect(csp).not.toMatch(grant);
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['cache-control']).toBe('no-store');
    expect(headers['content-disposition']).toBeUndefined();
  });

  it('matches the frame attribute to the header, and never grants an origin', () => {
    // Two independent layers that have to agree: the response's CSP and the `sandbox` the client
    // frames it with. `previewSandboxFor` is shared by both so they cannot drift apart.
    expect(previewSandbox('layout.html')).toBe('allow-scripts');
    expect(previewSandbox('plan.svg')).toBe('allow-scripts');
    expect(previewSandboxFor('document')).toBe('allow-scripts');
    // Everything this console renders itself gets nothing at all.
    for (const kind of ['markdown', 'text', 'image', 'none'] as const)
      expect(previewSandboxFor(kind)).toBe('');
    for (const name of ['BRIEF.md', 'photo.png', 'notes.bin'])
      expect(previewSandbox(name)).not.toMatch(/allow-same-origin/);
  });

  it('gives a file the console renders itself no scripts at all', () => {
    // An image or a markdown document is rendered by this console, not executed, so the permissive
    // policy above must not leak to it.
    const csp = previewHeaders('photo.png', false)['content-security-policy'] ?? '';
    expect(csp).toMatch(/\bsandbox;/);
    expect(csp).not.toMatch(/allow-scripts/);
    expect(csp).not.toMatch(/script-src/);
  });

  it('never grants scripts to a download, whatever the extension says', () => {
    const csp = previewHeaders('layout.html', true)['content-security-policy'] ?? '';
    expect(csp).not.toMatch(/allow-scripts/);
    expect(previewHeaders('layout.html', true)['content-type']).toBe('application/octet-stream');
  });

  it('hands over an unrenderable file as a download with a safe filename', () => {
    const headers = previewHeaders('weird name; rm -rf.bin', true);
    expect(headers['content-type']).toBe('application/octet-stream');
    expect(headers['content-disposition']).toBe('attachment; filename="weird_name__rm_-rf.bin"');
  });
});

describe('readBoundedWorkspaceFile', () => {
  it('refuses a named pipe without blocking the server waiting for a writer', () => {
    const ws = workspace();
    try {
      execFileSync('mkfifo', [join(ws.root, 'stream.txt')]);
      // A separate process gives the regression a deadline even when openSync blocks.
      const script = `import { readBoundedWorkspaceFile } from './src/server/workspace/files.ts';
        try { readBoundedWorkspaceFile(process.argv[1], 'stream.txt', 1024); process.exitCode = 1; }
        catch (e) { if (e.kind !== 'not-a-file') throw e; }`;
      const result = spawnSync(
        process.execPath,
        ['--import', 'tsx', '--input-type=module', '-e', script, ws.root],
        { timeout: 3000 },
      );
      expect(result.error).toBeUndefined();
      expect(result.status, result.stderr.toString()).toBe(0);
    } finally {
      ws.cleanup();
    }
  });

  it('lists an internal link with the target’s size and modification time', () => {
    const ws = workspace();
    try {
      const target = join(ws.root, 'BRIEF.md');
      symlinkSync(target, join(ws.root, 'brief-link.md'));
      const entry = listWorkspace(ws.root, '').entries.find((e) => e.name === 'brief-link.md');
      expect(entry?.size).toBe(fs.statSync(target).size);
      expect(entry?.modifiedAt).toBe(fs.statSync(target).mtimeMs);
    } finally {
      ws.cleanup();
    }
  });
  it('reads the bytes, and measures the file it actually opened', () => {
    const ws = workspace();
    try {
      const read = readBoundedWorkspaceFile(ws.root, 'BRIEF.md', 1024);
      expect(read.bytes.toString('utf8')).toBe('# Brief\n\nFit-out for a 60 m² cafe.\n');
      expect(read.path.endsWith('/BRIEF.md')).toBe(true);
    } finally {
      ws.cleanup();
    }
  });

  it('refuses a file over the limit rather than reading it to find out how big it is', () => {
    const ws = workspace();
    try {
      writeFileSync(join(ws.root, 'huge.log'), 'x'.repeat(4096));
      expect(() => readBoundedWorkspaceFile(ws.root, 'huge.log', 1024)).toThrow(WorkspaceError);
      expect(() => readBoundedWorkspaceFile(ws.root, 'huge.log', 1024)).toThrow(/over the 1024-byte limit/);
      // Exactly at the limit is fine: the extra byte requested only tells the two cases apart.
      writeFileSync(join(ws.root, 'exact.log'), 'x'.repeat(1024));
      expect(readBoundedWorkspaceFile(ws.root, 'exact.log', 1024).bytes).toHaveLength(1024);
    } finally {
      ws.cleanup();
    }
  });

  it('refuses a link out of the workspace and a directory, through the same gate as every read', () => {
    const ws = workspace();
    try {
      expect(() => readBoundedWorkspaceFile(ws.root, 'secrets-link.txt', 1024)).toThrow(
        /outside this session/,
      );
      expect(() => readBoundedWorkspaceFile(ws.root, 'escape/secrets.txt', 1024)).toThrow(
        /outside this session/,
      );
      expect(() => readBoundedWorkspaceFile(ws.root, 'handoff', 1024)).toThrow(/not a file/);
    } finally {
      ws.cleanup();
    }
  });
});
