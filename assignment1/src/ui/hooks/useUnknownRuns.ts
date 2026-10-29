'use client';
import { useEffect, useRef } from 'react';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';

/** The tRPC error code, when the failure carries one. */
function errorCode(err: unknown): string | undefined {
  const data = (err as { data?: { code?: unknown } } | null)?.data;
  return typeof data?.code === 'string' ? data.code : undefined;
}

/**
 * Runs first seen over the socket have no prompt, directory or provenance yet; fetch their DTOs.
 *
 * One request per run, not one per store update. `unknownRuns` changes whenever anything else in it
 * does — and a busy run changes the store many times a second — so without the in-flight set a
 * single unhydrated run meant a request per event. The set is keyed by run id and released when the
 * request settles, so a failure that is worth retrying (anything but "no such run") is retried on
 * the next change rather than never.
 */
export function useUnknownRuns() {
  const utils = trpc.useUtils();
  const unknown = useMissionStore((s) => s.unknownRuns);
  const asking = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const runId of unknown) {
      if (asking.current.has(runId)) continue;
      asking.current.add(runId);
      utils.client.runs.get
        .query({ runId })
        .then((dto) => {
          asking.current.delete(runId);
          useMissionStore.getState().hydrateRuns([dto]);
        })
        .catch((err: unknown) => {
          asking.current.delete(runId);
          // The server does not know this run (its lane was closed, or the database was replaced
          // under us). Asking again on every store update would be a request loop against a 404,
          // so forget it; anything else is worth another try on the next change.
          if (errorCode(err) === 'NOT_FOUND') useMissionStore.getState().forgetUnknownRun(runId);
        });
    }
  }, [unknown, utils]);
}
