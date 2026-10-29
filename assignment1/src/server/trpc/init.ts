import { initTRPC } from '@trpc/server';
import superjson from 'superjson';
import type { ServerContext } from '@/server/context';

// The builder itself is not the module's API — the three handles below are, so every procedure
// in the router is built from the same context and transformer.
const t = initTRPC.context<ServerContext>().create({ transformer: superjson });
export const router = t.router;
export const publicProcedure = t.procedure;
export const createCallerFactory = t.createCallerFactory;
