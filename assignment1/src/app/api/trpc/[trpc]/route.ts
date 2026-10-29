import { fetchRequestHandler } from '@trpc/server/adapters/fetch';
import { getServerContext } from '@/server/context';
import { appRouter } from '@/server/trpc/router';

export const dynamic = 'force-dynamic';

const handler = (req: Request) =>
  fetchRequestHandler({
    endpoint: '/api/trpc',
    req,
    router: appRouter,
    createContext: () => getServerContext(),
  });

export { handler as GET, handler as POST };
