import { isBrowserTool } from './classify';
import { type BrowserStatus, type BrowserView, CHROME_MCP_SERVER, type RunView } from './types';

/**
 * What one run can honestly say about the browser.
 *
 * The whole point of this function is that it never reads a request back as an answer, and never
 * reads an *attempt* back as a success. Three different things get confused otherwise:
 *
 * - `--chrome` on the command line is a request. The CLI starts perfectly normally when Chrome is
 *   not running, so the flag proves nothing.
 * - the tools appearing in `system/init` proves the CLI loaded the `claude-in-chrome` MCP server.
 *   It does not prove the extension is reachable: a disconnected browser still answers a tool call,
 *   with "Browser extension is not connected".
 * - a browser call that has not come back is neither: it is in flight.
 * - only a browser tool call that came back *without* an error is evidence that a browser did
 *   something.
 *
 * So the ladder below is ordered by evidence, and each rung comes from a different place: the run
 * row, the run's own init event, and the trajectory.
 */
export function deriveBrowserView(view: RunView): BrowserView {
  const calls = Object.values(view.callsById).filter((c) => isBrowserTool(c.name));
  const errorCount = calls.filter((c) => c.status === 'error').length;
  const successCount = calls.filter((c) => c.status === 'done').length;
  const pendingCount = calls.filter((c) => c.status === 'pending').length;
  const serverStatus = view.setup.mcpServers.find((s) => s.name === CHROME_MCP_SERVER)?.status;
  return {
    status: statusOf(view, { successCount, errorCount, pendingCount }),
    callCount: calls.length,
    successCount,
    errorCount,
    pendingCount,
    serverStatus,
    /** The latest failure's own words, which is the only useful thing to show about a disconnect. */
    lastError: calls.findLast((c) => c.status === 'error')?.result?.text?.slice(0, 300),
  };
}

function statusOf(
  view: RunView,
  counts: { successCount: number; errorCount: number; pendingCount: number },
): BrowserStatus {
  // A successful browser call outranks everything, including a run that was never flagged: Chrome
  // can be enabled by default in the operator's own CLI settings, and if a call worked, it worked.
  if (counts.successCount > 0) return 'used';
  // A call has actually come back an error, and none has succeeded. A real, showable state — it is
  // what a disconnected extension looks like — and calling it "in use" would be the overclaim this
  // type exists to prevent.
  if (counts.errorCount > 0) return 'attempted';
  // Called, nothing back yet. Every healthy Chrome run passes through this for a second or two, so
  // reporting it as failure put "calls failing" on the first browser action of a working session.
  if (counts.pendingCount > 0) return 'calling';
  if (view.browser !== 'chrome') return 'off';
  // `init` is the first event of every run, so its absence means the run has not started reporting
  // yet. Judging availability from an empty setup would flash a failure on every healthy run's
  // first frame; `toolCount` is the marker that init has actually been seen.
  if (view.setup.toolCount === undefined) return 'requested';
  return view.setup.browserTools.length > 0 ? 'loaded' : 'unavailable';
}

/**
 * What to tell the operator, for the states that are a problem.
 *
 * Deliberately says nothing about what the agent will do instead. A model that cannot reach the
 * browser may say so, may use another tool, or may stop; that is its decision, not a fact this
 * console can predict, and printing a prediction as though it were one would be its own overclaim.
 * What the console can do is report what is missing and what the trajectory actually contains.
 */
export function browserGuidance(status: BrowserStatus): string | undefined {
  if (status === 'unavailable')
    return 'This run started without the browser tools, so no browser action in it is possible. Check that the Claude in Chrome extension is installed and enabled, that Chrome is running, and that the CLI is signed in with /login — an API key or setup token disables the integration. Then run /chrome in a terminal Claude Code session and pick "Reconnect extension".';
  if (status === 'attempted')
    return 'A browser call came back an error and none has succeeded. If they keep failing, the extension is most likely not connected: run /chrome in a terminal Claude Code session and pick "Reconnect extension". The failures themselves are in the trajectory.';
  if (status === 'requested') return 'Waiting for the run to report which tools it loaded.';
  // `calling` is not a problem, so it gets no guidance and no banner. A first browser call takes a
  // moment, and telling the operator to reconnect while it is in flight would be advice to break a
  // working session.
  return undefined;
}
