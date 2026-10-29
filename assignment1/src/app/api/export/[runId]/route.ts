import { getServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';

export const dynamic = 'force-dynamic';

export async function GET(_req: Request, { params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const ctx = getServerContext();
  const run = repo.getRun(ctx.db, runId);
  if (!run) return new Response('not found', { status: 404 });
  const body = repo
    .listEvents(ctx.db, runId)
    .map((e) => e.json)
    .join('\n');
  return new Response(`${body}\n`, {
    headers: {
      'content-type': 'application/x-ndjson',
      'content-disposition': `attachment; filename="${runId}.jsonl"`,
    },
  });
}
