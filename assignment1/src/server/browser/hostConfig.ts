import { existsSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { join } from 'node:path';

/**
 * Whether Claude Code's native messaging host is already installed for a Chromium browser here.
 *
 * Read this as a weak, positive-only hint, because that is all it is. A live run on this machine
 * settled the question: before the first `--chrome` run the host file was *absent* (only the Claude
 * desktop app's own host was there), the run was started anyway, and the CLI installed its host and
 * connected without interrupting anything. So absence does not mean a run will fail and does not
 * mean the operator has something to install.
 *
 * That is why nothing here returns a warning, and why the interface must never turn a `false` into
 * one. The question "can this session drive the browser?" is answered by the run — the tools it
 * reports loading, and the browser calls that succeed (`deriveBrowserView`) — and a preflight guess
 * that contradicts the run is worse than no guess at all.
 *
 * Read-only by design: this console never writes the configuration, installs the extension, or
 * touches the operator's Chrome or their global Claude Code settings.
 */
export interface ChromeHostConfig {
  /** The host file was found for at least one browser. `false` is not evidence of a problem. */
  installed: boolean;
  /** Whether this platform's locations are known at all; Windows keeps them in the registry. */
  checked: boolean;
}

const HOST_FILE = 'com.anthropic.claude_code_browser_extension.json';

/**
 * The two locations the Chrome integration documentation gives exact paths for, per platform.
 *
 * Deliberately not a catalogue of every Chromium fork: each extra entry is a guess about someone
 * else's directory layout, and since the result is only ever a positive hint, a miss costs nothing
 * while a wrong path would just be noise to maintain.
 */
function locations(home: string): string[] {
  if (platform() === 'darwin') {
    const support = join(home, 'Library', 'Application Support');
    return [join(support, 'Google', 'Chrome'), join(support, 'Microsoft Edge')];
  }
  if (platform() === 'linux') {
    const config = join(home, '.config');
    return [join(config, 'google-chrome'), join(config, 'microsoft-edge')];
  }
  // Windows registers the host in the registry, which this does not read. `checked: false` means
  // "no opinion", which the interface renders as nothing at all.
  return [];
}

export function detectChromeHostConfig(home: string = homedir()): ChromeHostConfig {
  const places = locations(home);
  if (places.length === 0) return { installed: false, checked: false };
  const installed = places.some((dir) => {
    try {
      return existsSync(join(dir, 'NativeMessagingHosts', HOST_FILE));
    } catch {
      return false;
    }
  });
  return { installed, checked: true };
}
