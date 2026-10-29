/**
 * Where a link inside an agent's own markdown points.
 *
 * A handoff package is only a package if its index works: `HANDOFF.md` saying
 * `[Plumbing](proposals/plumbing.md)` has to open that proposal. Rendered naively, that href is a
 * *relative URL* and the browser resolves it against whatever page is showing the markdown — so
 * clicking it navigated Mission Control to `/lanes/proposals/plumbing.md` and produced a 404. The
 * same is true of the parent's final response, which routinely names the files it just wrote.
 *
 * So every href is classified before it is rendered, by this function, in one place:
 *
 * - `external` — it has a scheme or is protocol-relative. An official source the research cited is
 *   exactly that, and it must keep working unchanged.
 * - `workspace` — it resolves to a path inside this session's own directory. That is a file the
 *   console can open, and the caller turns it into one.
 * - `other` — an in-document anchor, or something that leaves the workspace. Rendered as the plain
 *   link it is, and never given workspace powers.
 *
 * Pure and path-only: deciding a link points inside the workspace is not the same as being allowed
 * to read it. The server re-checks every path it is asked for, resolving symlinks, in
 * `resolveInWorkspace`.
 */
export type WorkspaceLink =
  | { kind: 'workspace'; path: string; fragment?: string }
  | { kind: 'external'; href: string }
  | { kind: 'other'; href: string };

/** `scheme:` or `//host` — anything the browser would treat as an absolute URL. */
const ABSOLUTE_URL = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

export interface LinkContext {
  /** Directory of the document being rendered, relative to the workspace root. `''` is the root. */
  dir: string;
  /**
   * The session's working directory, so an absolute path the agent printed — which is how a model
   * usually refers to a file it just wrote — is recognised as one of its own outputs.
   */
  cwd: string;
}

export function resolveWorkspaceLink(href: string | undefined, ctx: LinkContext): WorkspaceLink {
  const raw = (href ?? '').trim();
  if (raw === '' || raw.startsWith('#')) return { kind: 'other', href: raw };
  if (ABSOLUTE_URL.test(raw)) {
    // `file:` URLs are not something this console opens, and treating one as external would hand a
    // local path to the browser as a navigation. Neither, so: other.
    return /^file:/i.test(raw) ? { kind: 'other', href: raw } : { kind: 'external', href: raw };
  }

  // A query string means nothing to a file on disk; a fragment does (it scrolls a document), so it
  // is kept separately rather than being made part of the name.
  const [beforeHash = '', fragment] = splitOnce(raw, '#');
  const [pathPart = ''] = splitOnce(beforeHash, '?');
  const decoded = decodePath(pathPart);
  if (decoded === null || decoded === '') return { kind: 'other', href: raw };

  const relative = decoded.startsWith('/')
    ? relativeToCwd(decoded, ctx.cwd)
    : normalize(`${ctx.dir ? `${ctx.dir}/` : ''}${decoded}`);
  // `null` means it is not inside the workspace: an absolute path somewhere else on the machine, or
  // a relative one that climbed out with `..`.
  if (relative === null) return { kind: 'other', href: raw };
  return fragment === undefined
    ? { kind: 'workspace', path: relative }
    : { kind: 'workspace', path: relative, fragment };
}

/**
 * The URL that serves one workspace file, with every segment encoded exactly once.
 *
 * `version` is how Refresh reaches an image or a framed document. The response already says
 * `cache-control: no-store`, but a browser only re-requests a subresource whose *URL* changed: an
 * `<img>` or an `<iframe>` whose `src` is identical across a re-render is simply left alone, and
 * the pane went on showing the drawing the agent had already replaced. The parameter is ignored by
 * the route — the path comes from the route segments, so nothing about containment depends on it.
 */
export function workspaceFileUrl(
  laneId: string,
  path: string,
  opts: { download?: boolean; version?: number } = {},
): string {
  const segments = path.split('/').map(encodeURIComponent).join('/');
  const query = [...(opts.download ? ['download=1'] : []), ...(opts.version ? [`v=${opts.version}`] : [])];
  return `/api/workspace/${encodeURIComponent(laneId)}/${segments}${query.length ? `?${query.join('&')}` : ''}`;
}

/** The directory part of a workspace-relative path; `''` for a file at the root. */
export function workspaceDir(path: string): string {
  const cut = path.lastIndexOf('/');
  return cut < 0 ? '' : path.slice(0, cut);
}

function splitOnce(value: string, sep: string): [string, string | undefined] {
  const at = value.indexOf(sep);
  return at < 0 ? [value, undefined] : [value.slice(0, at), value.slice(at + 1)];
}

/**
 * Markdown links are URLs, so a space is `%20`. A malformed escape is not a path this can open, and
 * it is returned as `null` rather than being passed through half-decoded.
 */
function decodePath(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

/** An absolute path expressed relative to `cwd`, or `null` when it is somewhere else entirely. */
function relativeToCwd(absolute: string, cwd: string): string | null {
  const root = cwd.replace(/\/+$/, '');
  if (root === '') return null;
  if (absolute === root) return '';
  if (!absolute.startsWith(`${root}/`)) return null;
  return normalize(absolute.slice(root.length + 1));
}

/** Resolve `.` and `..` within the workspace; `null` if the path climbs out of it. */
function normalize(path: string): string | null {
  const out: string[] = [];
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      if (out.length === 0) return null;
      out.pop();
      continue;
    }
    out.push(part);
  }
  return out.length === 0 ? null : out.join('/');
}
