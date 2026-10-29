import { closeSync, createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { getServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import { MAX_SERVE_BYTES, openWorkspaceFile, previewHeaders, WorkspaceError } from '@/server/workspace/files';

export const dynamic = 'force-dynamic';

/**
 * One file out of one session's workspace, for the preview frame and the download link.
 *
 * Why a route and not another tRPC procedure: a drawing has to arrive as bytes with a content type
 * the browser will render, and an `<img>`, an `<iframe>` or a download needs a URL. tRPC serves
 * neither.
 *
 * Everything dangerous about that is handled in two places and only two. `resolveInWorkspace`
 * decides whether the path is really inside this lane's directory, resolving symlinks first, so a
 * link the agent planted cannot walk out. `previewHeaders` decides how the bytes may be used: an
 * opaque sandbox origin, scripts for a self-contained document and nothing else, no network, and
 * `nosniff`. The client frames it with the matching `sandbox` attribute as well.
 *
 * The lane id chooses the root; the client never supplies one.
 */
export async function GET(req: Request, { params }: { params: Promise<{ laneId: string; path: string[] }> }) {
  const { laneId, path } = await params;
  const ctx = getServerContext();
  const lane = repo.getLane(ctx.db, laneId);
  if (!lane) return new Response('no such session', { status: 404 });

  // Next has already percent-decoded each segment, so the array holds the real file name. Decoding
  // again was a bug in both directions: a file named `100%.txt` threw `URIError` and was rejected,
  // and one named `a%2Fb.txt` would have been turned into a two-segment path. Segments are joined
  // with `/` because that is the separator `resolveInWorkspace` expects, and a segment that is
  // `..` — however it was written in the URL — is caught by the containment check there.
  const rel = path.join('/');

  let opened: ReturnType<typeof openWorkspaceFile>;
  try {
    opened = openWorkspaceFile(lane.cwd, rel);
  } catch (err) {
    if (!(err instanceof WorkspaceError)) throw err;
    return new Response(err.message, { status: err.kind === 'outside' ? 403 : 404 });
  }

  const { fd, path: full, stat } = opened;
  if (stat.size > MAX_SERVE_BYTES) {
    closeSync(fd);
    return new Response(
      `That file is ${Math.round(stat.size / 1_000_000)} MB, larger than this console will serve (${Math.round(MAX_SERVE_BYTES / 1_000_000)} MB).`,
      { status: 413 },
    );
  }

  const download = new URL(req.url).searchParams.get('download') === '1';
  const name = path.at(-1) ?? 'file';
  const headers = { ...previewHeaders(name, download), 'content-length': String(stat.size) };
  if (stat.size === 0) {
    closeSync(fd);
    return new Response('', { headers });
  }
  // Streamed rather than buffered, and stopped at exactly the length that was measured and
  // advertised. An agent may still be appending to this file: without `end`, a file that grew
  // between the `stat` and the read would send more bytes than `Content-Length` promises (which
  // browsers treat as a protocol error) and would slip past the size limit checked above.
  const body = Readable.toWeb(
    createReadStream(full, { fd, autoClose: true, start: 0, end: stat.size - 1 }),
  ) as ReadableStream;
  return new Response(body, { headers });
}
