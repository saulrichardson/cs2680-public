import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readdirSync,
  readSync,
  realpathSync,
  type Stats,
  statSync,
} from 'node:fs';
import { extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { type PreviewKind, previewSandboxFor } from '@/core/preview';

/**
 * Reading what an agent produced, without turning the console into a file server.
 *
 * A session's value is its output — a plan, a set of briefs, a drawing — and a printed file name is
 * not access to it. So this module exists to let the operator open those files. Everything it does
 * is bounded by one rule, enforced in one place (`resolveInWorkspace`): the only readable bytes are
 * the ones inside *this lane's own working directory*, as the filesystem resolves it.
 *
 * "As the filesystem resolves it" is the part that matters. Rejecting `..` in the request is not
 * enough on its own, because the agent can create a symlink inside its own workspace pointing at
 * anything the server user can read, and `resolve()` would happily hand back a path under the root
 * that `open()` then follows straight out of it. Every path is therefore `realpath`-ed and the
 * containment check is made against the real root, which collapses both traversal and symlink
 * escape into the same test. Both are covered by tests.
 */

/** How the browser should show a file. Chosen by extension: nothing here sniffs content. */
export type { PreviewKind } from '@/core/preview';

export interface WorkspaceEntry {
  name: string;
  /** Relative to the workspace root, `/`-separated, never absolute and never starting with `/`. */
  path: string;
  kind: 'file' | 'directory';
  size: number;
  modifiedAt: number;
  preview: PreviewKind;
  /**
   * A symlink whose target is outside the workspace. Listed rather than hidden — the operator
   * should be able to see that the agent made one — but never readable through this module.
   */
  blocked?: boolean;
}

export interface WorkspaceListing {
  /** The directory listed, relative to the root; `''` is the root itself. */
  path: string;
  entries: WorkspaceEntry[];
  /** More entries exist than `MAX_ENTRIES`; the listing is a prefix, not the directory. */
  truncated: boolean;
}

export class WorkspaceError extends Error {
  constructor(
    message: string,
    readonly kind: 'outside' | 'missing' | 'unreadable' | 'too-large' | 'not-a-file',
  ) {
    super(message);
    this.name = 'WorkspaceError';
  }
}

/** How many entries one directory listing will return. A listing is for reading, not for indexing. */
const MAX_ENTRIES = 500;

/** The largest file this will return as text. Larger ones are offered as a download instead. */
export const MAX_TEXT_BYTES = 512 * 1024;

/** The largest file the raw route will serve at all, preview or download. */
export const MAX_SERVE_BYTES = 32 * 1024 * 1024;

/**
 * Directories never worth listing: either enormous, or this console's own bookkeeping. Skipped
 * rather than blocked — an operator can still reach a file inside one by asking for it directly,
 * and `resolveInWorkspace` is what decides whether they may.
 */
const SKIP_DIRS = new Set(['.git', 'node_modules', '.next', '.data', '.data-e2e']);

const TEXT_EXTENSIONS = new Set([
  '.txt',
  '.log',
  '.csv',
  '.tsv',
  '.json',
  '.jsonl',
  '.yaml',
  '.yml',
  '.toml',
  '.ini',
  '.env',
  '.ts',
  '.tsx',
  '.js',
  '.jsx',
  '.mjs',
  '.cjs',
  '.py',
  '.rb',
  '.go',
  '.rs',
  '.java',
  '.sh',
  '.sql',
  '.css',
  '.scss',
  '.xml',
  '.gitignore',
]);
const IMAGE_TYPES: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
};
/** Rendered in a sandboxed frame, never as a top-level page. See `previewHeaders`. */
const DOCUMENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf',
};

export function previewKind(name: string): PreviewKind {
  const ext = extname(name).toLowerCase();
  if (ext === '.md' || ext === '.markdown') return 'markdown';
  if (ext in DOCUMENT_TYPES) return 'document';
  if (ext in IMAGE_TYPES) return 'image';
  if (TEXT_EXTENSIONS.has(ext) || ext === '') return 'text';
  return 'none';
}

/**
 * The content type the raw route serves a file as.
 *
 * Deliberately an allowlist that falls back to `application/octet-stream`: an unrecognised
 * extension is something to download, not something to let the browser decide how to execute. Sent
 * with `X-Content-Type-Options: nosniff`, so the browser does not decide either.
 */
export function contentTypeOf(name: string): string {
  const ext = extname(name).toLowerCase();
  if (ext === '.md' || ext === '.markdown') return 'text/markdown; charset=utf-8';
  if (ext in DOCUMENT_TYPES) return DOCUMENT_TYPES[ext] as string;
  if (ext in IMAGE_TYPES) return IMAGE_TYPES[ext] as string;
  if (TEXT_EXTENSIONS.has(ext)) return 'text/plain; charset=utf-8';
  return 'application/octet-stream';
}

/**
 * Headers for serving one workspace file to a browser.
 *
 * The agent wrote this file, and an HTML or SVG plan it generated is markup this console did not
 * review. The threat is not that it runs — a layout drawing with layer toggles is *supposed* to run,
 * and a preview whose controls are dead is a misleading preview. The threat is that it runs *as
 * Mission Control*: same-origin it could read this console's pages and drive its endpoints with the
 * operator's own session. So the document is given scripts and denied an origin.
 *
 * - `Content-Security-Policy: sandbox allow-scripts` puts the response in an **opaque** origin —
 *   never `allow-same-origin` — where its own scripts run but can reach nothing of ours. Forms,
 *   popups, pointer lock and top-level navigation stay off because they are simply not granted.
 * - `default-src 'none'` with `script-src 'unsafe-inline'` means a *self-contained* document works
 *   and a page that tries to pull in remote code or call home does not: no `connect-src`, no remote
 *   `script-src`, no external images or fonts beyond `data:`.
 * - `form-action 'none'` and `frame-ancestors 'self'` close the last two ways markup could send data
 *   somewhere or be embedded by something else.
 * - `nosniff` holds the content type to the allowlist above.
 * - The caller frames it with the matching `sandbox` attribute (`previewSandbox`), so neither layer
 *   is load-bearing on its own.
 *
 * Files the console does not render are served as a download instead, which is the safe way to hand
 * over bytes it will not interpret.
 */
export function previewHeaders(name: string, download: boolean): Record<string, string> {
  const interactive = !download && previewKind(name) === 'document';
  const csp = interactive
    ? "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; form-action 'none'; frame-ancestors 'self'"
    : "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; frame-ancestors 'self'";
  return {
    'content-type': download ? 'application/octet-stream' : contentTypeOf(name),
    'content-security-policy': csp,
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    // Never a cached view of a file an agent is still writing.
    'cache-control': 'no-store',
    ...(download ? { 'content-disposition': `attachment; filename="${name.replace(/[^\w.-]/g, '_')}"` } : {}),
  };
}

/**
 * The `sandbox` attribute the client must frame this kind with, kept beside the header it mirrors.
 *
 * `allow-scripts` *without* `allow-same-origin` is the whole trick: the document gets a unique
 * opaque origin, so its own controls work and it can touch nothing of this console's. Adding
 * `allow-same-origin` here would undo the protection entirely.
 */
export function previewSandbox(name: string): string {
  return previewSandboxFor(previewKind(name));
}

/**
 * A request for `rel` inside `root`, resolved to a real path that is provably inside it.
 *
 * The one gate. Every read and every listing goes through it, and it throws rather than returning
 * something the caller has to remember to check.
 */
export function resolveInWorkspace(root: string, rel: string): string {
  if (rel.includes('\0')) throw new WorkspaceError('That path is not a valid file name', 'outside');
  // An absolute request is not a path *inside* the workspace, whatever it happens to point at, and
  // `resolve(root, '/etc/passwd')` would return `/etc/passwd`. Refused before it is resolved.
  if (isAbsolute(rel)) throw new WorkspaceError('Only paths inside the workspace can be opened', 'outside');
  let realRoot: string;
  try {
    realRoot = realpathSync(root);
  } catch {
    throw new WorkspaceError(`${root} no longer exists`, 'missing');
  }
  const target = resolve(realRoot, rel);
  let real: string;
  try {
    // Resolves every symlink on the way, which is what makes the check below cover a link the agent
    // created inside its own workspace pointing anywhere else on this machine.
    real = realpathSync(target);
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === 'ENOENT' || code === 'ENOTDIR')
      throw new WorkspaceError(`${rel || '.'} is not in this workspace any more`, 'missing');
    throw new WorkspaceError(`${rel || '.'} could not be read`, 'unreadable');
  }
  if (real !== realRoot && !real.startsWith(realRoot + sep))
    throw new WorkspaceError('That path is outside this session’s workspace', 'outside');
  return real;
}

/** One directory of the workspace, directories first, then files, both by name. */
export function listWorkspace(root: string, rel: string): WorkspaceListing {
  const dir = resolveInWorkspace(root, rel);
  if (!statSync(dir).isDirectory()) throw new WorkspaceError(`${rel} is not a directory`, 'not-a-file');
  const realRoot = realpathSync(root);
  const names = readdirSync(dir).filter((n) => !SKIP_DIRS.has(n));
  const truncated = names.length > MAX_ENTRIES;
  const entries: WorkspaceEntry[] = [];
  for (const name of names.slice().sort().slice(0, MAX_ENTRIES)) {
    const entry = describe(realRoot, dir, name);
    if (entry) entries.push(entry);
  }
  entries.sort(
    (a, b) => Number(a.kind === 'file') - Number(b.kind === 'file') || a.name.localeCompare(b.name),
  );
  return { path: posix(relative(realRoot, dir)), entries, truncated };
}

function describe(realRoot: string, dir: string, name: string): WorkspaceEntry | null {
  const full = join(dir, name);
  let link: ReturnType<typeof lstatSync>;
  try {
    link = lstatSync(full);
  } catch {
    // Vanished between the readdir and the stat — an agent is working in here, after all.
    return null;
  }
  const base = {
    name,
    path: posix(relative(realRoot, full)),
    size: link.size,
    modifiedAt: link.mtimeMs,
  };
  if (link.isSymbolicLink()) {
    // Listed, so the operator can see it exists, but marked: `resolveInWorkspace` will refuse to
    // open it if it leaves the workspace, and this is what says so before they click.
    let escapes = true;
    try {
      const real = realpathSync(full);
      escapes = real !== realRoot && !real.startsWith(realRoot + sep);
      if (!escapes) {
        const target = statSync(full);
        return {
          ...base,
          size: target.size,
          modifiedAt: target.mtimeMs,
          kind: target.isDirectory() ? 'directory' : 'file',
          preview: target.isDirectory() ? 'none' : previewKind(name),
        };
      }
    } catch {
      escapes = true;
    }
    return { ...base, kind: 'file', preview: 'none', blocked: true };
  }
  return {
    ...base,
    kind: link.isDirectory() ? 'directory' : 'file',
    preview: link.isDirectory() ? 'none' : previewKind(name),
  };
}

export interface WorkspaceFile {
  path: string;
  name: string;
  size: number;
  modifiedAt: number;
  preview: PreviewKind;
  /** Present for `markdown` and `text`; other kinds are served by the raw route, not inlined. */
  text?: string;
  /** The file is longer than `MAX_TEXT_BYTES` and `text` holds only the beginning. */
  truncated: boolean;
}

/** One file's content, for the kinds the console renders itself. */
export function readWorkspaceFile(root: string, rel: string): WorkspaceFile {
  const { fd, stat } = openWorkspaceFile(root, rel);
  try {
    const name = rel.split('/').pop() ?? rel;
    const preview = previewKind(name);
    const base = { path: rel, name, size: stat.size, modifiedAt: stat.mtimeMs, preview };
    if (preview !== 'markdown' && preview !== 'text') return { ...base, truncated: false };
    const bytes = readBytes(fd, MAX_TEXT_BYTES + 1);
    return {
      ...base,
      text: bytes.subarray(0, MAX_TEXT_BYTES).toString('utf8'),
      truncated: bytes.length > MAX_TEXT_BYTES,
    };
  } finally {
    closeSync(fd);
  }
}

/**
 * Open a regular workspace file once. The caller owns and must close the descriptor.
 * Nonblocking open lets us reject a named pipe without waiting for another process to write to it.
 * Recheck containment and identity after opening so a replaced path is refused before reading.
 */
export function openWorkspaceFile(root: string, rel: string): { path: string; fd: number; stat: Stats } {
  const full = resolveInWorkspace(root, rel);
  let fd: number | undefined;
  try {
    fd = openSync(full, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const stat = fstatSync(fd);
    if (!stat.isFile()) throw new WorkspaceError(`${rel} is not a file`, 'not-a-file');
    const current = resolveInWorkspace(root, rel);
    const currentStat = statSync(current);
    if (current !== full || currentStat.dev !== stat.dev || currentStat.ino !== stat.ino)
      throw new WorkspaceError(`${rel} changed while it was being opened; try again`, 'unreadable');
    return { path: full, fd, stat };
  } catch (err) {
    if (fd !== undefined) closeSync(fd);
    if (err instanceof WorkspaceError) throw err;
    const code = (err as NodeJS.ErrnoException).code;
    if (code === 'ELOOP' || code === 'EMLINK')
      throw new WorkspaceError(`${rel} became a symbolic link while it was being read`, 'outside');
    if (code === 'ENOENT' || code === 'ENOTDIR')
      throw new WorkspaceError(`${rel} is not in this workspace any more`, 'missing');
    throw new WorkspaceError(`${rel} could not be read`, 'unreadable');
  }
}

/** Capture bounded bytes from the same descriptor whose size and type were checked. */
export function readBoundedWorkspaceFile(
  root: string,
  rel: string,
  limit: number,
): { path: string; bytes: Buffer } {
  const { path, fd, stat } = openWorkspaceFile(root, rel);
  try {
    if (stat.size > limit)
      throw new WorkspaceError(`${rel} is ${stat.size} bytes, over the ${limit}-byte limit`, 'too-large');
    const bytes = readBytes(fd, limit + 1);
    if (bytes.length > limit)
      throw new WorkspaceError(`${rel} grew past the ${limit}-byte limit while it was read`, 'too-large');
    return { path, bytes };
  } finally {
    closeSync(fd);
  }
}

/** Read up to the bound or EOF. A short read need not mean the file has ended. */
function readBytes(fd: number, limit: number): Buffer {
  const buffer = Buffer.allocUnsafe(limit);
  let offset = 0;
  while (offset < limit) {
    const count = readSync(fd, buffer, offset, limit - offset, offset);
    if (count === 0) break;
    offset += count;
  }
  return Buffer.from(buffer.subarray(0, offset));
}

/** A relative path with `/` separators, so one vocabulary reaches the browser on every platform. */
function posix(p: string): string {
  return sep === '/' ? p : p.split(sep).join('/');
}
