/**
 * The local-only boundary, as a decision about one request's headers.
 *
 * Mission Control listens on loopback and has no authentication of any kind: every request it
 * accepts can start an agent in a directory, read the operator's trajectories, and export them.
 * Loopback alone does not make that safe from a browser. Any page the operator visits can issue
 * cross-origin requests to `http://127.0.0.1:3000`, and a hostname the attacker controls can be
 * made to resolve to 127.0.0.1 (DNS rebinding) so that those requests look same-origin to the
 * browser. The readiness review confirmed the gap concretely: a WebSocket presenting
 * `Origin: https://review.invalid` was accepted.
 *
 * Two questions, both answered here so the HTTP and WebSocket entry points cannot disagree:
 *
 * - **Host** — which authority did the client think it was talking to? Only a loopback name on this
 *   server's own port is accepted, which is what stops a rebound hostname.
 * - **Origin** — which document is asking? Present means a browser is asking on behalf of a page;
 *   it must be this same origin. Absent means a non-browser tool (curl, a test, an editor
 *   integration) is asking directly, which is allowed: the header is not a credential and its
 *   absence is not a claim.
 *
 * This is a boundary, not an authorization scheme. It does not make the console safe to expose.
 */

export interface LocalBoundary {
  /** The port this server is listening on; an Origin or Host naming another port is not us. */
  port: number;
}

export type BoundaryVerdict = { ok: true } | { ok: false; status: 403 | 421; reason: string };

/** The header names this decision reads, lower-cased, as Node delivers them. */
export interface RequestHeaders {
  host?: string | string[] | undefined;
  origin?: string | string[] | undefined;
}

const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]', '::1', '[::ffff:127.0.0.1]']);

/**
 * Is this hostname one of the machine's own loopback names?
 *
 * Only the literal loopback addresses and `localhost`. Every other name — including one that
 * happens to resolve to 127.0.0.1 today — is refused, because what it resolves to is the
 * attacker's choice and not ours. The whole 127.0.0.0/8 block is accepted since the kernel routes
 * all of it to the loopback interface and a server bound to 127.0.0.1 can legitimately be reached
 * as, say, 127.0.0.2 on some platforms.
 */
export function isLoopbackHostname(hostname: string): boolean {
  const name = hostname.toLowerCase();
  if (LOOPBACK_HOSTNAMES.has(name)) return true;
  const ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(name);
  if (!ipv4) return false;
  const octets = ipv4.slice(1).map(Number);
  return octets.every((n) => n <= 255) && octets[0] === 127;
}

/** Split an authority into hostname and port, keeping an IPv6 literal's brackets intact. */
function splitAuthority(authority: string): { hostname: string; port: string } | null {
  if (authority.length === 0) return null;
  if (authority.startsWith('[')) {
    const end = authority.indexOf(']');
    if (end === -1) return null;
    const rest = authority.slice(end + 1);
    if (rest !== '' && !rest.startsWith(':')) return null;
    return { hostname: authority.slice(0, end + 1), port: rest.slice(1) };
  }
  const parts = authority.split(':');
  if (parts.length > 2) return null;
  return { hostname: parts[0] ?? '', port: parts[1] ?? '' };
}

function first(value: string | string[] | undefined): string | undefined {
  // Node collapses repeated headers, but a duplicated `Host` is a request smuggling shape rather
  // than an ambiguity to resolve: refuse it by treating the array as unusable.
  return typeof value === 'string' ? value : undefined;
}

/**
 * May this request be served?
 *
 * `421 Misdirected Request` is the honest answer for a Host this server does not answer to, and
 * `403` for a page that is not this console asking on someone's behalf.
 */
export function checkLocalBoundary(headers: RequestHeaders, boundary: LocalBoundary): BoundaryVerdict {
  const host = first(headers.host);
  if (host === undefined) return { ok: false, status: 421, reason: 'no Host header' };
  const authority = splitAuthority(host.trim());
  if (!authority) return { ok: false, status: 421, reason: `malformed Host ${JSON.stringify(host)}` };
  if (!isLoopbackHostname(authority.hostname))
    return { ok: false, status: 421, reason: `Host ${JSON.stringify(host)} is not a loopback address` };
  if (authority.port !== String(boundary.port))
    return { ok: false, status: 421, reason: `Host ${JSON.stringify(host)} is not this server's port` };

  const origin = first(headers.origin);
  // No Origin: not a browser acting for a page. `curl`, an editor integration, the Playwright
  // fixtures and the console's own WebSocket in some browsers all arrive this way.
  if (origin === undefined) return { ok: true };
  // "null" is what a browser sends for a sandboxed iframe, a `file://` document or a redirected
  // cross-origin request. It names no origin that could be ours.
  if (origin === 'null') return { ok: false, status: 403, reason: 'Origin: null' };
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return { ok: false, status: 403, reason: `malformed Origin ${JSON.stringify(origin)}` };
  }
  if (parsed.protocol !== 'http:') return { ok: false, status: 403, reason: `Origin ${origin} is not http` };
  if (!isLoopbackHostname(parsed.hostname))
    return { ok: false, status: 403, reason: `Origin ${origin} is not local` };
  if (parsed.port !== String(boundary.port))
    return { ok: false, status: 403, reason: `Origin ${origin} is not this server` };
  return { ok: true };
}
