import { describe, expect, it } from 'vitest';
import { browserGuidance, deriveBrowserView } from '@/core/browser';
import { applyEnvelopes, createRunView } from '@/core/reducer';
import type { BrowserMode, RunView } from '@/core/types';
import { DelegationStream } from '../helpers/delegation';

const CHROME_TOOLS = [
  'mcp__claude-in-chrome__navigate',
  'mcp__claude-in-chrome__read_page',
  'mcp__claude-in-chrome__browser_batch',
];

function run(browser: BrowserMode, build: (s: DelegationStream) => DelegationStream): RunView {
  const base = createRunView({
    runId: 'run-1',
    laneId: 'lane-1',
    prompt: 'p',
    cwd: '/tmp',
    startedAt: 0,
    browser,
  });
  return applyEnvelopes(base, build(new DelegationStream()).envelopes);
}

describe('deriveBrowserView', () => {
  it('says nothing about a session that never asked for the browser', () => {
    const view = deriveBrowserView(run('off', (s) => s.init()));
    expect(view.status).toBe('off');
    expect(view.callCount).toBe(0);
    expect(browserGuidance('off')).toBeUndefined();
  });

  it('is only "requested" until the run reports what it loaded', () => {
    // No init yet: the flag was passed and nothing has come back. Judging availability here would
    // flash a failure on the first frame of every healthy run.
    const view = deriveBrowserView(run('chrome', (s) => s));
    expect(view.status).toBe('requested');
  });

  it('calls it unavailable when init arrives without browser tools, and says what to do', () => {
    const view = deriveBrowserView(run('chrome', (s) => s.init()));
    expect(view.status).toBe('unavailable');
    expect(view.serverStatus).toBeUndefined();
    const guidance = browserGuidance('unavailable');
    // The guidance has to name the real causes from the Chrome integration's own troubleshooting:
    // the extension, the browser, and the login mode that silently disables the integration.
    expect(guidance).toMatch(/extension/i);
    expect(guidance).toMatch(/\/chrome/);
    expect(guidance).toMatch(/login|API key/i);
    // And it must *not* predict what the model will do instead. Whether it substitutes another
    // tool, says it cannot, or stops is its decision; printing a guess as though it were a fact is
    // the same overclaim in the opposite direction.
    expect(guidance).not.toMatch(/WebFetch/);
  });

  it('is "loaded" — not "used" — when the CLI reports the tools but nothing has called them', () => {
    const view = deriveBrowserView(run('chrome', (s) => s.init({ browserTools: CHROME_TOOLS })));
    expect(view.status).toBe('loaded');
    expect(view.serverStatus).toBe('connected');
    expect(view.callCount).toBe(0);
  });

  it('is "used" only once a browser tool call has actually succeeded', () => {
    const view = deriveBrowserView(
      run('chrome', (s) =>
        s.init({ browserTools: CHROME_TOOLS }).browserCall('b1', 'navigate').browserCall('b2', 'read_page'),
      ),
    );
    expect(view.status).toBe('used');
    expect(view.callCount).toBe(2);
    expect(view.successCount).toBe(2);
    expect(view.errorCount).toBe(0);
  });

  it('is "calling", not failing, while the first browser call is still in flight', () => {
    // The live regression: the very first Chrome call of a healthy run has no answer for a second or
    // two. Reporting that as failure put "calls failing" on the chip above a banner that read
    // "0 browser calls failed and none has succeeded".
    const view = deriveBrowserView(
      run('chrome', (s) => s.init({ browserTools: CHROME_TOOLS }).browserCallPending('b1', 'navigate')),
    );
    expect(view.status).toBe('calling');
    expect(view.pendingCount).toBe(1);
    expect(view.successCount).toBe(0);
    expect(view.errorCount).toBe(0);
    expect(view.lastError).toBeUndefined();
    // Nothing is wrong, so there is nothing to advise and nothing to warn about.
    expect(browserGuidance('calling')).toBeUndefined();
  });

  it('becomes "attempted" only when a call has actually come back an error', () => {
    // One failed, one still in flight: a real failure has happened, so this is the state that warns
    // — and the pending call is still counted as pending rather than as a second failure.
    const view = deriveBrowserView(
      run('chrome', (s) =>
        s
          .init({ browserTools: CHROME_TOOLS })
          .browserCall('b1', 'navigate', null, true)
          .browserCallPending('b2', 'read_page'),
      ),
    );
    expect(view.status).toBe('attempted');
    expect(view.errorCount).toBe(1);
    expect(view.pendingCount).toBe(1);
    expect(view.callCount).toBe(2);
  });

  it('leaves "calling" for "used" the moment the call comes back clean', () => {
    const pending = deriveBrowserView(
      run('chrome', (s) => s.init({ browserTools: CHROME_TOOLS }).browserCallPending('b1', 'navigate')),
    );
    expect(pending.status).toBe('calling');
    const answered = deriveBrowserView(
      run('chrome', (s) => s.init({ browserTools: CHROME_TOOLS }).browserCall('b1', 'navigate')),
    );
    expect(answered.status).toBe('used');
    expect(answered.pendingCount).toBe(0);
  });

  it('is "attempted", never "used", while every browser call is coming back an error', () => {
    // What a disconnected extension looks like from here: the tools are loaded, the calls are made,
    // and each one answers "Browser extension is not connected". Calling that Chrome in use would
    // put a tick beside research that never reached a browser.
    const view = deriveBrowserView(
      run('chrome', (s) =>
        s
          .init({ browserTools: CHROME_TOOLS })
          .browserCall('b1', 'navigate', null, true)
          .browserCall('b2', 'navigate', null, true),
      ),
    );
    expect(view.status).toBe('attempted');
    expect(view.successCount).toBe(0);
    expect(view.errorCount).toBe(2);
    expect(view.lastError).toContain('not connected');
    expect(browserGuidance('attempted')).toMatch(/Reconnect extension/);
  });

  it('is "used" as soon as one call succeeds, even alongside failures', () => {
    const view = deriveBrowserView(
      run('chrome', (s) =>
        s
          .init({ browserTools: CHROME_TOOLS })
          .browserCall('b1', 'navigate', null, true)
          .browserCall('b2', 'navigate'),
      ),
    );
    expect(view.status).toBe('used');
    expect(view.successCount).toBe(1);
    expect(view.errorCount).toBe(1);
  });

  it('counts browser calls a delegate made, and the ones that came back as errors', () => {
    const view = deriveBrowserView(
      run('chrome', (s) =>
        s
          .init({ browserTools: CHROME_TOOLS })
          .delegate('toolu_a', 'task_a', { description: 'Permits', prompt: 'p' })
          .taskStarted('toolu_a', 'task_a', { description: 'Permits' })
          .browserCall('b1', 'navigate', 'toolu_a')
          .browserCall('b2', 'navigate', 'toolu_a', true),
      ),
    );
    // A child's browser call is still this run's browser call: one CLI process, one extension.
    expect(view.callCount).toBe(2);
    expect(view.successCount).toBe(1);
    expect(view.errorCount).toBe(1);
    expect(view.status).toBe('used');
  });

  it('believes a browser call even when the run was not flagged', () => {
    // Chrome can be enabled by default in the operator's own CLI settings, which this console does
    // not read. A call that happened, happened.
    const view = deriveBrowserView(run('off', (s) => s.init().browserCall('b1', 'navigate')));
    expect(view.status).toBe('used');
  });
});
