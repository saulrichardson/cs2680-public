'use client';
import { Globe, TriangleAlert } from 'lucide-react';
import { browserGuidance } from '@/core/browser';
import type { BrowserView } from '@/core/types';

/**
 * What the browser actually did in this run. Read-only, and shown only when it says something.
 *
 * There is no setting to display any more — every execution is launched with the browser — so the
 * only honest thing left to report is evidence: calls that succeeded, calls that are failing, or
 * tools that never loaded. States that mean "nothing has happened yet" (`requested`, `loaded`,
 * `off`) and the transient `calling` are silent: an indicator that appears on every run and says
 * nothing is exactly the ambient noise this interface is being cleared of.
 *
 * Never "configured therefore connected". The evidence comes from the run's own trajectory.
 */
const EVIDENCE: Record<BrowserView['status'], string | null> = {
  off: null,
  requested: null,
  unavailable: 'no browser tools',
  loaded: null,
  calling: null,
  attempted: 'calls failing',
  used: 'browser calls',
};

export function BrowserEvidence({ browser }: { browser: BrowserView }) {
  const evidence = EVIDENCE[browser.status];
  if (!evidence) return null;
  const counts =
    browser.callCount > 0
      ? `${browser.successCount}/${browser.callCount}${browser.errorCount > 0 ? ` · ${browser.errorCount} failed` : ''}`
      : null;
  return (
    <span className="browser-status" data-testid="browser-status">
      <span
        className={`pill pill-evidence-${browser.status}`}
        title={
          browser.lastError
            ? `Latest browser failure: ${browser.lastError}`
            : (browserGuidance(browser.status) ??
              'Counted from this run’s own trajectory: successful calls out of calls made.')
        }
        data-testid="browser-evidence"
        data-status={browser.status}
      >
        <Globe size={11} /> {counts ? `${counts} ${evidence}` : evidence}
      </span>
    </span>
  );
}

/**
 * The unmissable version, for the one state that is both actionable and about work that happened.
 *
 * `attempted` means the agent called a browser tool and every call came back an error — what a
 * disconnected extension looks like. Whatever the session says about a web page did not come from
 * a browser, so it is worth a banner.
 *
 * `unavailable` deliberately does *not* raise one. Now that every run is launched with the
 * browser, a machine without the extension installed would show this banner on every run,
 * including the ones that never wanted a browser — turning ordinary work into a permanent
 * warning. It stays as the quiet evidence chip instead. A call merely in flight gets nothing at
 * all: that is what every healthy browser run looks like for a second or two.
 */
export function BrowserWarning({ browser }: { browser: BrowserView }) {
  if (browser.status !== 'attempted') return null;
  return (
    <div className="browser-warning" role="alert" data-testid="browser-warning" data-status={browser.status}>
      <TriangleAlert size={14} />
      <div>
        <strong>
          {browser.errorCount} browser call{browser.errorCount === 1 ? '' : 's'} failed and none has
          succeeded.
        </strong>
        <p>{browserGuidance(browser.status)}</p>
        {browser.lastError && (
          <p className="mono" data-testid="browser-last-error">
            {browser.lastError}
          </p>
        )}
      </div>
    </div>
  );
}
