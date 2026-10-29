import { describe, expect, it } from 'vitest';
import { checkLocalBoundary, isLoopbackHostname } from '@/server/net/localOnly';

const boundary = { port: 3000 };
const check = (headers: { host?: string; origin?: string }) => checkLocalBoundary(headers, boundary);

describe('isLoopbackHostname', () => {
  it('accepts the machine own names for itself', () => {
    for (const name of ['localhost', 'LOCALHOST', '127.0.0.1', '127.0.0.2', '[::1]', '[::ffff:127.0.0.1]'])
      expect(isLoopbackHostname(name)).toBe(true);
  });

  it('refuses every name whose meaning someone else controls', () => {
    // The point of the rule: what a hostname resolves to is the attacker's choice, not ours.
    for (const name of ['example.com', 'localhost.evil.com', '127.0.0.1.nip.io', '0.0.0.0', '10.0.0.1', ''])
      expect(isLoopbackHostname(name)).toBe(false);
  });
});

describe('the local-only boundary', () => {
  it('serves a request from the console itself', () => {
    expect(check({ host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000' })).toEqual({ ok: true });
    expect(check({ host: 'localhost:3000', origin: 'http://localhost:3000' })).toEqual({ ok: true });
  });

  it('serves ordinary navigation and local tools, which send no Origin at all', () => {
    // A top-level GET in a browser, `curl`, an editor integration: the header is not a credential
    // and its absence is not a claim about anyone.
    expect(check({ host: '127.0.0.1:3000' })).toEqual({ ok: true });
  });

  /**
   * The readiness review's reproduction: a page on the open internet opening a WebSocket to the
   * operator's console. Loopback is not a boundary against a browser — the browser is already
   * inside it.
   */
  it('refuses a page that is not this console', () => {
    for (const origin of [
      'https://review.invalid',
      'http://evil.example',
      'http://127.0.0.1:4000',
      'https://127.0.0.1:3000',
      'http://localhost.evil.com:3000',
    ]) {
      const verdict = check({ host: '127.0.0.1:3000', origin });
      expect(verdict.ok).toBe(false);
      if (!verdict.ok) expect(verdict.status).toBe(403);
    }
  });

  it('refuses an origin that names nobody, or nothing parseable', () => {
    // `null` is what a sandboxed iframe, a `file://` document, or a redirected cross-origin
    // request presents. It is not this console.
    for (const origin of ['null', 'not a url', '://', '']) {
      const verdict = check({ host: '127.0.0.1:3000', origin });
      expect(verdict.ok).toBe(false);
      if (!verdict.ok) expect(verdict.status).toBe(403);
    }
  });

  /**
   * DNS rebinding: the attacker's own hostname, pointed at 127.0.0.1. The browser believes it is
   * same-origin and sends no cross-origin hints at all, so the Host header is the only place this
   * can be caught.
   */
  it('refuses a Host this server does not answer to', () => {
    for (const host of ['rebind.example:3000', 'evil.com', '127.0.0.1:4000', 'localhost:80', '[::1]']) {
      const verdict = check({ host });
      expect(verdict.ok).toBe(false);
      if (!verdict.ok) expect(verdict.status).toBe(421);
    }
  });

  it('refuses a missing or malformed Host rather than guessing', () => {
    for (const headers of [{}, { host: '' }, { host: '[::1:3000' }, { host: 'a:b:c' }])
      expect(check(headers).ok).toBe(false);
    // a repeated Host header is a smuggling shape, not an ambiguity to resolve
    expect(checkLocalBoundary({ host: ['127.0.0.1:3000', 'evil.com'] }, boundary).ok).toBe(false);
  });

  it('says why, in words that name the header', () => {
    const rebound = check({ host: 'rebind.example:3000' });
    expect(rebound.ok).toBe(false);
    if (!rebound.ok) expect(rebound.reason).toContain('loopback');
    const foreign = check({ host: '127.0.0.1:3000', origin: 'https://review.invalid' });
    expect(foreign.ok).toBe(false);
    if (!foreign.ok) expect(foreign.reason).toContain('review.invalid');
  });
});
