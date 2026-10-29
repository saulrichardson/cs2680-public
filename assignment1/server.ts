import { createServer } from 'node:http';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import next from 'next';
import { loadConfig } from './src/server/config';
import { createServerContext, registerServerContext, type ShutdownReport } from './src/server/context';
import { checkLocalBoundary } from './src/server/net/localOnly';

const appDir = dirname(fileURLToPath(import.meta.url));
const dev = process.env.NODE_ENV !== 'production';
const config = loadConfig(process.env, appDir);

async function main() {
  const ctx = createServerContext(config);
  registerServerContext(ctx);

  const app = next({ dev, dir: appDir, hostname: config.host, port: config.port });
  const handle = app.getRequestHandler();
  await app.prepare();
  // Next 16's `getUpgradeHandler()` reads server state populated by `prepare()` and throws
  // ("prepare() must be called before performing this operation") if called earlier, unlike
  // `getRequestHandler()`, which only needs `prepare()` to have run by the time a request arrives.
  const upgrade = app.getUpgradeHandler();

  // The local-only boundary sits in front of *everything*: pages, the tRPC API, the export route,
  // the WebSocket, and Next's own dev assets and HMR socket. This console has no authentication,
  // so the only thing standing between a page on the open internet and the operator's agents is
  // the decision in `checkLocalBoundary` — and a rule that only some entry points applied would
  // just move the way in. See that module for what is refused and why.
  const boundary = { port: config.port };

  // Admission, not liveness. Shutdown owns what it owns through `ctx` (see `OwnedWork`): a
  // response ending is not the work ending, so counting sockets here would say the server owns
  // nothing the moment a browser navigated away from a race it was still preparing. All this flag
  // does is stop *new* work arriving once shutdown has begun.
  let closing: Promise<ShutdownReport> | null = null;
  const server = createServer((req, res) => {
    if (closing) {
      res.writeHead(503, { 'content-type': 'text/plain; charset=utf-8', connection: 'close' });
      res.end('Mission Control is shutting down.\n');
      return;
    }
    const verdict = checkLocalBoundary(req.headers, boundary);
    if (!verdict.ok) {
      console.warn(`refused ${req.method} ${req.url}: ${verdict.reason}`);
      res.writeHead(verdict.status, { 'content-type': 'text/plain; charset=utf-8' });
      res.end(`Mission Control serves this machine only (${verdict.reason}).\n`);
      return;
    }
    handle(req, res).catch((err) => {
      console.error('request failed', err);
      if (!res.headersSent) res.writeHead(500);
      res.end('internal error');
    });
  });

  server.on('upgrade', (req, socket, head) => {
    // `upgrade` (Next's handler) is async, and a raw `server.on('upgrade', ...)` listener has no
    // one awaiting it, so an unhandled rejection here would crash the whole process (and every
    // live agent run with it). `new URL(...)` can also throw synchronously on a malformed
    // request target, so contain both paths and just drop the connection on failure.
    const fail = (err: unknown) => {
      console.warn('upgrade failed', err);
      if (!socket.destroyed) socket.destroy();
    };
    try {
      if (closing) {
        socket.end('HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n');
        return;
      }
      const verdict = checkLocalBoundary(req.headers, boundary);
      if (!verdict.ok) {
        console.warn(`refused upgrade ${req.url}: ${verdict.reason}`);
        // An upgrade has no response object yet, so the refusal is written by hand. The browser
        // reports it as a failed WebSocket handshake, which is what it is.
        socket.end(`HTTP/1.1 ${verdict.status} Forbidden\r\nConnection: close\r\n\r\n`);
        return;
      }
      const pathname = new URL(req.url ?? '/', 'http://localhost').pathname;
      if (pathname === '/ws') ctx.hub.handleUpgrade(req, socket, head);
      else if (pathname.startsWith('/_next')) void Promise.resolve(upgrade(req, socket, head)).catch(fail);
      else socket.destroy();
    } catch (err) {
      fail(err);
    }
  });

  server.listen(config.port, config.host, () => {
    console.log(`Mission Control  http://${config.host}:${config.port}`);
    console.log(`  data: ${config.dataDir}`);
    console.log(`  agent: ${config.claudeBin}`);
  });

  // One shutdown, however many signals arrive. Two SIGINTs used to run this twice, closing SQLite
  // under the second and leaving whatever the first was still reaping without an owner.
  const shutdown = (signal: string): Promise<ShutdownReport> => {
    closing ??= (async () => {
      console.log(`\n${signal}: shutting down`);
      // Admission first: stop accepting connections, and hang up the idle keep-alives that would
      // otherwise keep the server "open" indefinitely. The `closing` flag set above refuses
      // anything that arrives on a connection already established.
      server.close();
      server.closeIdleConnections?.();
      // Then the operations themselves: agents reaped, comparisons cancelled and settled, request
      // work waited for, and only then the database closed. `ctx.close` says whether it could
      // confirm all of that.
      const report = await ctx.close();
      server.closeAllConnections?.();
      return report;
    })();
    return closing;
  };
  const stop = (signal: string) => {
    void shutdown(signal).then(
      // An unconfirmed shutdown exits non-zero. Whatever supervises this process — a shell, a
      // terminal, `pnpm start` — should be able to tell "it stopped" from "it gave up while it was
      // still doing something", and only the exit code carries that.
      (report) => process.exit(report.ok ? 0 : 1),
      (err) => {
        console.error('shutdown failed', err);
        process.exit(1);
      },
    );
  };
  process.on('SIGINT', () => stop('SIGINT'));
  process.on('SIGTERM', () => stop('SIGTERM'));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
