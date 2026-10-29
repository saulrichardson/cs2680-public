import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { isLoopbackHostname } from './net/localOnly';

export interface Config {
  host: string;
  port: number;
  dataDir: string;
  dbFile: string;
  claudeBin: string;
  allowedTools?: string;
  /**
   * Where "New scratch folder" creates folders.
   *
   * Visible and predictable rather than hidden in a temp directory: an operator should be able to
   * find what the agent wrote without asking the console where it put it. Overridable so the
   * browser suite can point it inside the directory that run owns — an automated test must not
   * create folders in the real `~/scratch`.
   */
  scratchRoot: string;
}

/**
 * `Number(env.PORT)` alone turned `PORT=http` into `NaN` and `PORT=0` into a random port, both of
 * which surface much later as a confusing listen failure. Fail at startup with the reason instead.
 */
function parsePort(raw: string | undefined): number {
  if (!raw) return 3000;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error(`PORT must be an integer between 1 and 65535, got ${JSON.stringify(raw)}`);
  return port;
}

/**
 * A configured path as an absolute one, anchored on the app directory rather than on whatever
 * `process.cwd()` happens to be.
 *
 * The data directory crosses two boundaries that resolve a relative path differently: `git worktree
 * add <path>` runs with the *target repository* as its cwd, so it creates the tree under the
 * repository, while the process manager and SQLite resolve the same string against the app's cwd.
 * One configured directory then means two places on disk (readiness review R9). Normalizing once,
 * at the edge where configuration is parsed, is what keeps every consumer talking about the same
 * directory.
 */
function appPath(appDir: string, path: string): string {
  return isAbsolute(path) ? resolve(path) : resolve(appDir, path);
}

/**
 * The address to listen on, which may only ever be a loopback one.
 *
 * Mission Control has no authentication: anything that can reach it can start an agent in a
 * directory on this machine and read every trajectory it holds. Binding it to an address other
 * people can reach is not a configuration choice this build supports, so `HOST=0.0.0.0` is refused
 * at startup with the reason rather than quietly obeyed. Exposing the console would need
 * authenticated access designed for it, not a different bind address.
 */
function parseHost(raw: string | undefined): string {
  const host = raw ?? '127.0.0.1';
  if (isLoopbackHostname(host)) return host;
  throw new Error(
    `HOST must be a loopback address (got ${JSON.stringify(host)}). Mission Control has no ` +
      'authentication and is not safe to expose; see docs/approach.md.',
  );
}

export function loadConfig(env: NodeJS.ProcessEnv, appDir: string): Config {
  const dataDir = appPath(appDir, env.MISSION_CONTROL_DATA_DIR ?? join(appDir, '.data'));
  return {
    host: parseHost(env.HOST),
    port: parsePort(env.PORT),
    dataDir,
    dbFile: join(dataDir, 'mission-control.db'),
    claudeBin: env.MISSION_CONTROL_CLAUDE_BIN ?? 'claude',
    allowedTools: env.MISSION_CONTROL_ALLOWED_TOOLS,
    scratchRoot: appPath(appDir, env.MISSION_CONTROL_SCRATCH_ROOT ?? join(homedir(), 'scratch')),
  };
}
