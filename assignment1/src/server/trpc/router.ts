import { lstatSync, mkdirSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { TRPCError } from '@trpc/server';
import { nanoid } from 'nanoid';
import { z } from 'zod';
import { isTerminal } from '@/core/status';
import type { LaneDto, RunSummaryDto } from '@/core/types';
import {
  needsPartialAcknowledgement,
  planWrapUp,
  sourceOutcome,
  sourceRevision,
  WRAPUP_MAX_FILES,
  WRAPUP_MAX_INSTRUCTIONS,
  WRAPUP_MAX_SOURCES,
  WRAPUP_VERSION,
  type WrapUpSourceFacts,
} from '@/core/wrapup';
import { detectChromeHostConfig } from '@/server/browser/hostConfig';
import type { ServerContext } from '@/server/context';
import * as repo from '@/server/db/repo';
import type { LaneRow, RunRow } from '@/server/db/schema';
import {
  describeCheckout,
  headCommit,
  isGitRepo,
  prepareStandaloneWorktree,
  prepareWorktrees,
  removeWorktree,
  resolveRepoRoot,
} from '@/server/git/worktrees';
import { listWorkspace, readWorkspaceFile, WorkspaceError } from '@/server/workspace/files';
import { captureWrapUp, removeWrapUpPackage, WrapUpError } from '@/server/wrapup/capture';
import { createCallerFactory, publicProcedure, router } from './init';

const Permission = z.enum(['allowlist', 'bypass']);
/** How many subdirectories the picker will list for one directory. */
const MAX_BROWSE_ENTRIES = 500;
const RunOptions = z.object({
  maxTurns: z.number().int().min(1).max(200).optional(),
  model: z.string().min(1).max(100).optional(),
});

/**
 * Why a directory could not be browsed. A directory that exists but cannot be read is a different
 * problem from a typo, and the picker has no way to tell them apart from `exists: false` alone.
 */
function describeFsError(err: unknown): string {
  const code = (err as NodeJS.ErrnoException | null)?.code;
  if (code === 'EACCES' || code === 'EPERM') return 'is not readable';
  if (code === 'ENOENT' || code === 'ENOTDIR') return 'does not exist';
  return `could not be read: ${err instanceof Error ? err.message : String(err)}`;
}

/**
 * Resolve a user-supplied working directory, refusing the two that turn an agent loose over
 * everything the user owns: `$HOME` and the filesystem root. The client mirrors this message.
 *
 * Symlinks are resolved when the directory exists, so `/tmp/x` and `/private/tmp/x` are one
 * project rather than two lists of half its sessions. A path that does *not* exist is kept as
 * written: starting a session against a directory that is not there is a supported way to see a
 * run fail visibly, and canonicalising it away would turn that into a different error.
 */
function projectDir(input: string): string {
  const path = resolve(input);
  // Checked twice, because a symlink is a second name for the same directory: `~/link-to-home`
  // resolves to `$HOME` and would otherwise walk straight past a boundary that exists to stop an
  // agent being turned loose over everything the operator owns.
  const refuse = (p: string) => p === resolve(homedir()) || p === '/';
  let canonical = path;
  try {
    canonical = realpathSync(path);
  } catch {
    // Not there. Kept as written: starting a session against a folder that does not exist is a
    // supported way to see a run fail visibly, and canonicalising it away changes that error.
  }
  if (refuse(path) || refuse(canonical))
    throw new TRPCError({
      code: 'BAD_REQUEST',
      message: 'Choose a project folder, not your home folder or the filesystem root',
    });
  return canonical;
}

/**
 * A name not already taken in `parent`: `scratch`, then `scratch-2`, and so on.
 *
 * `lstat`, so a dangling symlink counts as taken. `stat` follows the link, fails, and would offer
 * a name `mkdir` then refuses with EEXIST.
 */
function freeFolderName(parent: string, stem: string): string {
  for (let n = 1; n < 1000; n += 1) {
    const name = n === 1 ? stem : `${stem}-${n}`;
    try {
      lstatSync(join(parent, name));
    } catch {
      return name;
    }
  }
  return `${stem}-${Date.now()}`;
}

/**
 * The directory a new folder may be created in.
 *
 * An explicit parent has to exist already — it came from browsing, so if it is not there the
 * request is about a path that was typed or is stale, and creating the chain to it is exactly the
 * silent `mkdir -p` this avoids. The configured scratch root is the one exception: it is the
 * console's own default location, and it is provisioned on first use.
 */
function folderParent(ctx: ServerContext, explicit: string | undefined): string {
  if (explicit === undefined) {
    mkdirSync(ctx.config.scratchRoot, { recursive: true });
    return realpathSync(ctx.config.scratchRoot);
  }
  const parent = resolve(explicit);
  try {
    if (statSync(parent).isDirectory()) return realpathSync(parent);
  } catch (err) {
    throw new TRPCError({ code: 'BAD_REQUEST', message: `${parent} ${describeFsError(err)}` });
  }
  throw new TRPCError({ code: 'BAD_REQUEST', message: `${parent} is not a directory` });
}

/** One folder name, not a path: the only thing "New folder" is allowed to create. */
const FOLDER_NAME = z
  .string()
  .trim()
  .min(1)
  .max(80)
  .refine((n) => !n.includes('/') && !n.includes('\\') && n !== '.' && n !== '..', {
    message: 'Use a single folder name, without slashes',
  });

/**
 * Zod's `.refine` proves the pair is exclusive but cannot narrow either branch's type, so the one
 * the branch just established is present is asserted here rather than defaulted to something.
 */
function nonNull<T>(v: T | undefined): T {
  if (v === undefined) throw new Error('unreachable: the input schema requires one of the two');
  return v;
}

/** The three things a client can ask about by id, with a message a person can act on. */
const GONE = {
  lane: 'That lane no longer exists',
  run: 'That run no longer exists',
  race: 'That race no longer exists',
} as const;

function notFound(what: keyof typeof GONE): TRPCError {
  return new TRPCError({ code: 'NOT_FOUND', message: GONE[what] });
}

/**
 * The one operation a lane is running right now, or `undefined` when it is idle.
 *
 * A lane runs at most one thing at a time, whatever kind of thing it is. A second `claude` in the
 * same directory would edit the same files from under the first; a replay or an import arriving
 * mid-run pushes a row the lane's own Stop then targets instead of the agent, which is how an
 * import could take cancellation away from a working agent (readiness review R2). One rule, one
 * answer, and every entry point asks the same question.
 *
 * The question is *ownership*, not what the row says. Whether the server still holds a process for
 * this run is the only thing that decides whether its directory is free, and the two can disagree
 * in the case that matters most: a fatal persistence failure writes the run `failed` immediately
 * while the agent is still being reaped. Asking the row would free the lane at exactly the moment
 * we know least about what is still running in it. A row left behind by a crash never appears here
 * either, because after a crash the server owns nothing.
 */
export function activeOperation(ctx: ServerContext, laneId: string): RunRow | undefined {
  return repo.listRuns(ctx.db, laneId).find((r) => ctx.processes.isLive(r.id) || ctx.replayer.isLive(r.id));
}

/**
 * The directory a new independent session should branch from, given the lane the operator asked
 * from. A closed lane still answers: its directory is still on disk, and "another session in this
 * project" is a perfectly good thing to want after closing the first one.
 */
function sourceCheckout(ctx: ServerContext, laneId: string): string {
  const lane = repo.getLane(ctx.db, laneId);
  if (!lane) throw notFound('lane');
  return lane.cwd;
}

/**
 * The directory whose files a lane may show, which is the directory its agent actually works in.
 *
 * A closed lane still answers: the work it produced is still on disk and is exactly what an
 * operator comes back for. What decides access is the lane's own `cwd`, never a path from the
 * client.
 */
export function workspaceRoot(ctx: ServerContext, laneId: string): string {
  const lane = repo.getLane(ctx.db, laneId);
  if (!lane) throw notFound('lane');
  return lane.cwd;
}

/** A workspace refusal as the status the client should show, keeping the reason it gave. */
function workspaceError(err: unknown): TRPCError {
  if (!(err instanceof WorkspaceError))
    return new TRPCError({
      code: 'INTERNAL_SERVER_ERROR',
      message: err instanceof Error ? err.message : String(err),
    });
  const code = err.kind === 'outside' ? 'FORBIDDEN' : err.kind === 'missing' ? 'NOT_FOUND' : 'BAD_REQUEST';
  return new TRPCError({ code, message: err.message });
}

function assertLaneIdle(ctx: ServerContext, laneId: string): void {
  const active = activeOperation(ctx, laneId);
  if (!active) return;
  // When the run is over but its processes could not be confirmed gone, say that rather than "a run
  // is in progress": the operator is owed the actual reason their lane is unavailable.
  const cleanup = ctx.processes.cleanupErrorOf(active.id);
  throw new TRPCError({
    code: 'PRECONDITION_FAILED',
    message: cleanup ?? 'A run is already in progress in this lane',
  });
}

/**
 * An archived session is read-only. `repo.getLane` finds archived rows too — it has to, so their
 * history and files can still be read — so every entry point that *starts* something asks this
 * instead of merely asking whether the session exists.
 */
function openLane(ctx: ServerContext, laneId: string): LaneRow {
  const lane = repo.getLane(ctx.db, laneId);
  if (!lane) throw notFound('lane');
  if (lane.archivedAt !== null)
    throw new TRPCError({
      code: 'PRECONDITION_FAILED',
      message: 'This session is archived. Reopen it to continue.',
    });
  return lane;
}

/**
 * Stop whatever is driving this run, by what the run actually is.
 *
 * Trying the process manager and then falling through to the replayer happened to work while the
 * two could not hold the same id, but it is guesswork: the run row says which one owns it, so ask
 * it. A run nothing is driving answers `false`, which is what the console reports as "Nothing to
 * stop — the run had already ended".
 */
async function stopRun(ctx: ServerContext, run: RunRow): Promise<boolean> {
  switch (run.origin) {
    case 'execution':
      return ctx.processes.cancel(run.id);
    case 'replay':
      return ctx.replayer.cancel(run.id);
    // An import is written in its terminal state inside one transaction; there is never a moment
    // at which one is in flight to stop.
    case 'import':
      return false;
  }
}

function toLaneDto(ctx: ServerContext, lane: LaneRow): LaneDto {
  return {
    id: lane.id,
    name: lane.name,
    cwd: lane.cwd,
    projectRoot: lane.projectRoot,
    permission: lane.permission,
    groupId: lane.groupId,
    groupIndex: lane.groupIndex,
    createdAt: lane.createdAt,
    lastActivityAt: repo.lastActivityAt(ctx.db, lane),
    archivedAt: lane.archivedAt,
    isolated: repo.getWorktree(ctx.db, lane.id) !== undefined,
  };
}

/**
 * A name worth reading in a list, without asking a model for one.
 *
 * The operator's own name wins. Otherwise the folder, numbered only when that folder already has
 * sessions — "notes", "notes 2" — which is how a person would name them and is stable enough to
 * find a conversation by.
 */
function sessionName(ctx: ServerContext, provided: string | undefined, cwd: string): string {
  if (provided?.trim()) return provided.trim();
  const base = basename(cwd) || cwd;
  const siblings = repo.listLanes(ctx.db, { includeArchived: true, projectRoot: cwd }).length;
  return siblings === 0 ? base : `${base} ${siblings + 1}`;
}

function toDto(run: RunRow): RunSummaryDto {
  return {
    ...repo.lifecycleOf(run),
    prompt: run.prompt,
    cwd: run.effectiveCwd,
    resumedFrom: run.resumedFrom ?? undefined,
    replayOf: run.replayOf ?? undefined,
    model: run.model ?? undefined,
    groupId: run.groupId ?? undefined,
    browser: run.browser,
    resumable: repo.isResumeCandidate(run),
  };
}

/**
 * What the server knows about one session as a wrap-up source, right now.
 *
 * The dialog and the capture ask the same function, so the button the dialog disables and the
 * refusal the mutation would give are decided by one set of facts. `revision` is the token the
 * operator's choice is pinned to (see `sourceRevision`); `busy` is asked of ownership rather than
 * of the row, for the same reason `assertLaneIdle` is.
 */
function wrapUpSourceFacts(ctx: ServerContext, laneId: string): WrapUpSourceFacts & { runs: number } {
  const lane = repo.getLane(ctx.db, laneId);
  if (!lane) throw notFound('lane');
  const executions = repo.listExecutions(ctx.db, laneId);
  return {
    laneId: lane.id,
    name: lane.name,
    projectRoot: lane.projectRoot,
    revision: sourceRevision(executions),
    outcome: sourceOutcome(executions),
    busy: activeOperation(ctx, lane.id) !== undefined,
    runs: executions.length,
  };
}

/** A capture refusal as the status the client should show, keeping the reason it gave. */
function wrapUpError(err: unknown): TRPCError {
  if (err instanceof TRPCError) return err;
  if (err instanceof WrapUpError) return new TRPCError({ code: err.code, message: err.message });
  return new TRPCError({
    code: 'INTERNAL_SERVER_ERROR',
    message: err instanceof Error ? err.message : String(err),
  });
}

export const appRouter = router({
  system: router({
    // Just the home directory, and from `os.homedir()` — the same source `projectDir` refuses a
    // lane in, so the client's warning and the server's rule can never disagree. The agent binary,
    // the data directory and the live-run list were never read by the client and are not the
    // browser's business.
    // The home directory comes from `os.homedir()` — the same source `projectDir` refuses a lane
    // in, so the client's warning and the server's rule can never disagree. `chrome` is a
    // before-you-start hint about this machine's setup and is labelled as one wherever it appears:
    // it says whether the native messaging host was ever installed, not whether anything will
    // connect. Only a run can answer that (see `deriveBrowserView`).
    info: publicProcedure.query(() => ({ home: homedir(), chrome: detectChromeHostConfig() })),
  }),

  /**
   * What a session produced, read back from its own working directory.
   *
   * Scoped to one lane and gated by `resolveInWorkspace`, which resolves symlinks before deciding
   * whether a path is inside the workspace. Nothing here can read the rest of the machine.
   */
  outputs: router({
    list: publicProcedure
      .input(z.object({ laneId: z.string(), path: z.string().max(4096).default('') }))
      .query(({ ctx, input }) => {
        const root = workspaceRoot(ctx, input.laneId);
        try {
          return { root, ...listWorkspace(root, input.path) };
        } catch (err) {
          throw workspaceError(err);
        }
      }),
    read: publicProcedure
      .input(z.object({ laneId: z.string(), path: z.string().min(1).max(4096) }))
      .query(({ ctx, input }) => {
        const root = workspaceRoot(ctx, input.laneId);
        try {
          return readWorkspaceFile(root, input.path);
        } catch (err) {
          throw workspaceError(err);
        }
      }),
  }),

  fs: router({
    browse: publicProcedure.input(z.object({ path: z.string().min(1) })).query(async ({ input }) => {
      const path = resolve(input.path);
      let dirs: string[] = [];
      let exists = false;
      let error: string | undefined;
      let truncated = false;
      try {
        exists = statSync(path).isDirectory();
        if (exists) {
          const all = readdirSync(path, { withFileTypes: true })
            .filter((d) => d.isDirectory() && !d.name.startsWith('.'))
            .map((d) => d.name)
            .sort();
          // The picker is a list, not a file manager: cap it, but say so, or a directory with
          // thousands of children silently loses the one the user was looking for.
          truncated = all.length > MAX_BROWSE_ENTRIES;
          dirs = all.slice(0, MAX_BROWSE_ENTRIES);
        } else error = 'is not a directory';
      } catch (err) {
        exists = false;
        error = describeFsError(err);
      }
      return {
        path,
        exists,
        error,
        parent: dirname(path),
        name: basename(path),
        dirs,
        truncated,
        isGitRepo: exists ? await isGitRepo(path) : false,
      };
    }),

    /** Where "New folder" would put things, and a name that is not taken there yet. */
    scratchSuggestion: publicProcedure
      .input(z.object({ parent: z.string().trim().min(1).optional() }).optional())
      .query(({ ctx, input }) => {
        const parent = input?.parent ? resolve(input.parent) : ctx.config.scratchRoot;
        return { parent, name: freeFolderName(parent, 'scratch') };
      }),

    /**
     * Create one folder and answer with its path, so an operator can start work without leaving
     * for a terminal.
     *
     * Deliberately narrow: one validated name inside one parent that already exists. Not a path —
     * `../` and embedded separators are refused by the schema — and not a `mkdir -p` of whatever
     * was typed, which is how a mistyped folder becomes a real one nobody meant to make. The one
     * parent this will bring into being is the console's own configured scratch root, because
     * offering a default location that does not exist yet is offering nothing.
     *
     * A name already in use is refused rather than reused: "create" that quietly hands back
     * somebody else's folder is how an agent ends up in the wrong one. No git initialisation:
     * where files live and whether they are version-controlled are separate choices.
     */
    createFolder: publicProcedure
      .input(z.object({ parent: z.string().trim().min(1).optional(), name: FOLDER_NAME }))
      .mutation(({ ctx, input }) => {
        const parent = folderParent(ctx, input.parent);
        // User-chosen folders are runtime data, never files to trace into the application bundle.
        const path = join(/* turbopackIgnore: true */ parent, input.name);
        // `resolve` has already collapsed `..`; this catches a name that escaped anyway.
        if (dirname(path) !== parent)
          throw new TRPCError({ code: 'BAD_REQUEST', message: 'Use a single folder name, without slashes' });
        try {
          mkdirSync(path);
        } catch (err) {
          const code = (err as NodeJS.ErrnoException).code;
          if (code === 'EEXIST')
            throw new TRPCError({
              code: 'CONFLICT',
              message: `${path} already exists. Choose another name, or pick that folder instead.`,
            });
          throw new TRPCError({
            code: code === 'EACCES' || code === 'EPERM' ? 'FORBIDDEN' : 'INTERNAL_SERVER_ERROR',
            message: `Could not create ${path}: it ${describeFsError(err)}`,
          });
        }
        // Canonical, so the folder the operator is handed is the one a session will record.
        return { path: realpathSync(/* turbopackIgnore: true */ path) };
      }),
  }),

  projects: router({
    /** Every folder that has sessions, most recently active first. Home's list. */
    list: publicProcedure.query(({ ctx }) => repo.listProjects(ctx.db)),
  }),

  lanes: router({
    /**
     * Sessions, optionally narrowed to one project.
     *
     * The narrowing is the point. Without it, opening a session in a new scratch folder showed
     * every unrelated conversation on the machine as though it were part of that work. `archived`
     * is opt-in for the same reason: an archived session is history, not current work.
     */
    list: publicProcedure
      .input(
        z
          .object({
            projectRoot: z.string().optional(),
            /**
             * Sessions whose agent works in exactly this folder, whatever project they belong to.
             *
             * The question behind "who else is writing here", and it has to be asked of the folder
             * rather than of the project: an isolated session's checkout is its own folder, and a
             * second conversation started in that checkout shares its files just as two ordinary
             * sessions in one folder do.
             */
            cwd: z.string().optional(),
            includeArchived: z.boolean().optional(),
          })
          .optional(),
      )
      .query(({ ctx, input }) => {
        const cwd = input?.cwd === undefined ? undefined : projectDir(input.cwd);
        return repo
          .listLanes(ctx.db, {
            includeArchived: input?.includeArchived,
            ...(input?.projectRoot === undefined ? {} : { projectRoot: projectDir(input.projectRoot) }),
          })
          .filter((lane) => cwd === undefined || lane.cwd === cwd)
          .map((lane) => toLaneDto(ctx, lane));
      }),
    /**
     * One session by id, archived ones included.
     *
     * `list` answers with a project's *active* work, which is what the rail shows. A direct link
     * to an archived session still has to open its conversation and files, so the page that
     * renders one asks for it directly rather than concluding from its absence that it is gone.
     */
    get: publicProcedure.input(z.object({ laneId: z.string() })).query(({ ctx, input }) => {
      const lane = repo.getLane(ctx.db, input.laneId);
      if (!lane) throw notFound('lane');
      return toLaneDto(ctx, lane);
    }),
    create: publicProcedure
      .input(
        z.object({
          name: z.string().min(1).max(80).optional(),
          /** The folder to work in. Any folder: no git, no initialisation, no dialog. */
          cwd: z.string().trim().min(1),
          /**
           * Continue in the project this session belongs to. Given, the new session inherits its
           * project and — unless `cwd` says otherwise — its folder. A genuinely different folder
           * establishes that folder's own scope instead.
           */
          fromLaneId: z.string().optional(),
        }),
      )
      .mutation(({ ctx, input }) => {
        const cwd = projectDir(input.cwd);
        const source = input.fromLaneId ? repo.getLane(ctx.db, input.fromLaneId) : undefined;
        if (input.fromLaneId && !source) throw notFound('lane');
        // Inherited only when the new session really is in the source's own folder. A different
        // folder is a different project, whatever it was started from.
        const projectRoot = source && source.cwd === cwd ? source.projectRoot : cwd;
        const created = toLaneDto(
          ctx,
          repo.createLane(ctx.db, {
            id: nanoid(10),
            name: sessionName(ctx, input.name, cwd),
            cwd,
            projectRoot,
            permission: 'allowlist',
            createdAt: Date.now(),
          }),
        );
        ctx.hub.publishLanesChanged();
        return created;
      }),
    update: publicProcedure
      .input(z.object({ laneId: z.string(), name: z.string().min(1).max(80) }))
      .mutation(({ ctx, input }) => {
        const lane = repo.getLane(ctx.db, input.laneId);
        if (!lane) throw notFound('lane');
        repo.updateLane(ctx.db, input.laneId, { name: input.name });
        ctx.hub.publishLanesChanged();
        return toLaneDto(ctx, repo.getLane(ctx.db, input.laneId) as LaneRow);
      }),
    /**
     * Put an idle session away. Its runs, its events and its files all stay.
     *
     * Archiving is not a way to stop work, and it never cancels any: a session the server is still
     * driving is refused, with the reason. That includes a run whose outcome is already written
     * but whose processes are still being reaped — the directory is still occupied, and hiding the
     * session would hide the only place that says so.
     */
    archive: publicProcedure.input(z.object({ laneId: z.string() })).mutation(({ ctx, input }) => {
      const lane = repo.getLane(ctx.db, input.laneId);
      if (!lane) throw notFound('lane');
      const active = activeOperation(ctx, lane.id);
      if (active)
        throw new TRPCError({
          code: 'PRECONDITION_FAILED',
          message:
            ctx.processes.cleanupErrorOf(active.id) ??
            'This session is still running. Stop it first, then archive it.',
        });
      if (lane.archivedAt === null) repo.updateLane(ctx.db, lane.id, { archivedAt: Date.now() });
      ctx.hub.publishLanesChanged();
      return toLaneDto(ctx, repo.getLane(ctx.db, lane.id) as LaneRow);
    }),
    reopen: publicProcedure.input(z.object({ laneId: z.string() })).mutation(({ ctx, input }) => {
      const lane = repo.getLane(ctx.db, input.laneId);
      if (!lane) throw notFound('lane');
      if (lane.archivedAt !== null) repo.updateLane(ctx.db, lane.id, { archivedAt: null });
      ctx.hub.publishLanesChanged();
      return toLaneDto(ctx, repo.getLane(ctx.db, lane.id) as LaneRow);
    }),
  }),

  /**
   * A session that works in a checkout of its own.
   *
   * The consequential half of session creation: where the files are. An ordinary session shares
   * the project's folder; this one gets a git worktree at the project's committed HEAD, so the
   * operator's working files are untouched. It stays in the same project either way — a checkout
   * under the console's data directory is where the work happens, not what it belongs to.
   */
  workspaces: router({
    create: publicProcedure
      .input(
        z
          .object({
            /**
             * Isolate the project this session belongs to. Its own folder may already be a
             * checkout this console created, which is why the repository is resolved rather than
             * assumed — and why the operator is never asked to find the directory again.
             */
            fromLaneId: z.string().optional(),
            /** Or name the folder outright, which is what Home does. */
            repoRoot: z.string().trim().min(1).optional(),
            name: z.string().min(1).max(80).optional(),
          })
          .refine((v) => (v.fromLaneId === undefined) !== (v.repoRoot === undefined), {
            message: 'Name either a session to branch from or a folder, not both',
          }),
      )
      .mutation(async ({ ctx, input }) => {
        const laneId = nanoid(10);
        // Everything is inside `track`, the database read included, and deliberately so: this
        // reads rows, runs git and writes a checkout to disk, none of which may happen after
        // shutdown has taken its inventory of what the server owns. Reading the lane first meant a
        // request arriving during shutdown failed with "the database connection is not open"
        // instead of saying that the server was shutting down.
        return ctx.work.track(`workspace ${laneId}`, async (signal) => {
          const from = input.fromLaneId ? repo.getLane(ctx.db, input.fromLaneId) : undefined;
          if (input.fromLaneId && !from) throw notFound('lane');
          // Where the checkout comes from, and what it starts at, are two different questions: the
          // repository is the one `git worktree add` must run in, and the base is the committed
          // HEAD of the directory the operator started from.
          const source = from ? from.cwd : projectDir(nonNull(input.repoRoot));
          if (!(await isGitRepo(source)))
            throw new TRPCError({
              code: 'BAD_REQUEST',
              message: `${source} is not a git repository, so it cannot have a checkout of its own. A session in the folder itself works for any project.`,
            });
          const [repoRoot, baseCommit] = await Promise.all([
            resolveRepoRoot(source, signal),
            headCommit(source, signal),
          ]);
          const { path, branch } = await prepareStandaloneWorktree({
            repoRoot,
            baseCommit,
            laneId,
            dataDir: ctx.config.dataDir,
            signal,
          });
          // A project may be a subdirectory of its repository — a package inside a monorepo is a
          // perfectly good project boundary, and widening it to the git root would point the agent
          // at the whole repository instead. So the checkout is of the repository (git has no
          // smaller unit) while the session runs at the *corresponding* subdirectory inside it.
          // Cleanup ownership stays at the worktree root, which is what git registered.
          const within = relative(repoRoot, source);
          const cwd = within === '' || within.startsWith('..') ? path : join(path, within);
          // Prepared and then abandoned before anything could run in it: the one moment at which
          // removing this destroys no work.
          const discard = async (why: string): Promise<never> => {
            await removeWorktree(repoRoot, path, branch).catch((err) =>
              console.warn(`workspace ${laneId}: could not remove unused checkout ${path}`, err),
            );
            throw new TRPCError({ code: 'INTERNAL_SERVER_ERROR', message: why });
          };
          if (signal.aborted) return discard('The session was abandoned: the server is shutting down');
          // The project is the folder the operator chose, not the checkout this created. An
          // isolated session started from a session belongs to that session's project.
          const projectRoot = from ? from.projectRoot : source;
          let lane: LaneRow;
          try {
            lane = ctx.db.transaction((tx) => {
              const created = repo.createLane(tx, {
                id: laneId,
                name: sessionName(ctx, input.name, projectRoot),
                cwd,
                projectRoot,
                permission: 'allowlist',
                createdAt: Date.now(),
              });
              // No group: this is not a race, and inventing a one-member one would put it in
              // Compare and give it a candidate's keep/supersede semantics it has no business with.
              repo.insertWorktree(tx, {
                laneId,
                groupId: null,
                repoRoot,
                path,
                branch,
                baseCommit,
              });
              return created;
            });
          } catch (err) {
            return discard(
              `Could not record the session: ${err instanceof Error ? err.message : String(err)}`,
            );
          }
          ctx.hub.publishLanesChanged();
          return { lane: toLaneDto(ctx, lane), repoRoot, path, cwd, branch, baseCommit };
        });
      }),
    /**
     * What creating a session here would branch from, asked before anything is created.
     *
     * The honest half of the feature. `git worktree add <commit>` copies the committed state and
     * nothing else, so this reports the commit it would start at *and* how many files in the source
     * checkout are modified or untracked and would therefore stay behind. The interface shows both;
     * neither this nor the creation path commits anything on the operator's behalf.
     */
    preview: publicProcedure
      .input(
        z
          .object({ fromLaneId: z.string().optional(), repoRoot: z.string().trim().min(1).optional() })
          .refine((v) => (v.fromLaneId === undefined) !== (v.repoRoot === undefined), {
            message: 'Name either a lane to continue from or a repository, not both',
          }),
      )
      .query(async ({ ctx, input }) => {
        const source = input.fromLaneId
          ? sourceCheckout(ctx, input.fromLaneId)
          : projectDir(nonNull(input.repoRoot));
        if (!(await isGitRepo(source))) return { source, isGitRepo: false as const };
        try {
          return { source, isGitRepo: true as const, ...(await describeCheckout(source)) };
        } catch (err) {
          throw new TRPCError({
            code: 'BAD_REQUEST',
            message: `Could not read ${source}: ${err instanceof Error ? err.message : String(err)}`,
          });
        }
      }),
    /** What a lane's checkout is, for the header that has to explain it. `null` when it has none. */
    get: publicProcedure.input(z.object({ laneId: z.string() })).query(({ ctx, input }) => {
      const w = repo.getWorktree(ctx.db, input.laneId);
      if (!w) return null;
      return { ...w, repoRoot: repo.worktreeRepoRoot(ctx.db, input.laneId) };
    }),
  }),

  runs: router({
    start: publicProcedure
      .input(z.object({ laneId: z.string(), prompt: z.string().min(1).max(20_000) }).extend(RunOptions.shape))
      .mutation(async ({ ctx, input }) => {
        const lane = openLane(ctx, input.laneId);
        assertLaneIdle(ctx, lane.id);
        const runId = nanoid(12);
        await ctx.processes.start({
          runId,
          laneId: lane.id,
          prompt: input.prompt,
          cwd: lane.cwd,
          permission: lane.permission as 'allowlist' | 'bypass',
          maxTurns: input.maxTurns,
          model: input.model,
          groupId: lane.groupId ?? undefined,
        });
        return { runId };
      }),
    resume: publicProcedure
      .input(z.object({ laneId: z.string(), prompt: z.string().min(1).max(20_000) }).extend(RunOptions.shape))
      .mutation(async ({ ctx, input }) => {
        const lane = openLane(ctx, input.laneId);
        assertLaneIdle(ctx, lane.id);
        const sessionId = repo.latestSessionId(ctx.db, lane.id);
        if (!sessionId)
          throw new TRPCError({
            code: 'PRECONDITION_FAILED',
            message: 'this lane has no session to resume yet',
          });
        // Which run this follow-up continues, for the trajectory's "resumed" link. Only executions
        // qualify: a replay of that run is a faithful copy and carries the same session id, but it
        // is not the turn being continued.
        const previous = repo
          .listExecutions(ctx.db, lane.id)
          .filter((r) => r.sessionId === sessionId)
          .at(-1);
        const runId = nanoid(12);
        await ctx.processes.start({
          runId,
          laneId: lane.id,
          prompt: input.prompt,
          cwd: lane.cwd,
          permission: lane.permission as 'allowlist' | 'bypass',
          resumeSessionId: sessionId,
          resumedFrom: previous?.id,
          maxTurns: input.maxTurns,
          model: input.model,
          groupId: lane.groupId ?? undefined,
        });
        return { runId };
      }),
    cancel: publicProcedure.input(z.object({ runId: z.string() })).mutation(async ({ ctx, input }) => {
      const run = repo.getRun(ctx.db, input.runId);
      if (!run) throw notFound('run');
      return { cancelled: await stopRun(ctx, run) };
    }),
    get: publicProcedure.input(z.object({ runId: z.string() })).query(({ ctx, input }) => {
      const run = repo.getRun(ctx.db, input.runId);
      if (!run) throw notFound('run');
      return toDto(run);
    }),
    list: publicProcedure
      .input(z.object({ laneId: z.string() }))
      .query(({ ctx, input }) => repo.listRuns(ctx.db, input.laneId).map(toDto)),
    events: publicProcedure
      .input(z.object({ runId: z.string(), afterSeq: z.number().int().min(0).optional() }))
      .query(({ ctx, input }) => {
        const run = repo.getRun(ctx.db, input.runId);
        if (!run) throw notFound('run');
        return repo
          .listEvents(ctx.db, input.runId, input.afterSeq ?? 0)
          .map((r) => repo.toEnvelope(r, run.laneId));
      }),
  }),

  fanout: router({
    start: publicProcedure
      .input(
        z
          .object({
            repoRoot: z.string().min(1),
            prompt: z.string().min(1).max(20_000),
            laneCount: z.number().int().min(2).max(6),
            permission: Permission.optional(),
          })
          .extend(RunOptions.shape),
      )
      .mutation(async ({ ctx, input }) => {
        const groupId = nanoid(8);
        const repoRoot = projectDir(input.repoRoot);
        if (!(await isGitRepo(repoRoot))) {
          throw new TRPCError({ code: 'BAD_REQUEST', message: `${repoRoot} is not a git repository` });
        }
        const permission = input.permission ?? 'allowlist';
        // From here the request owns work that outlives its response: worktrees on disk, rows
        // in the database, and agents. `ctx.work` holds that ownership until the work settles,
        // so a browser that navigates away mid-preparation cannot make shutdown believe there
        // is nothing left to wait for.
        return ctx.work.track(`fan-out ${groupId}`, async (signal) => {
          // `signal` is aborted when the server starts shutting down. Preparation is several git
          // commands, each with a budget of its own, so it is stopped rather than waited out — and
          // the two things this operation owes are honoured either way: worktrees nothing has run
          // in are removed, and nothing new is started after the abort.
          const { baseCommit, worktrees } = await prepareWorktrees({
            repoRoot,
            groupId,
            count: input.laneCount,
            dataDir: ctx.config.dataDir,
            signal,
          });
          if (signal.aborted) {
            // Prepared, then abandoned before a single agent existed: exactly the case where the
            // worktrees are disposable. The removal itself is bounded and not cancellable.
            for (const w of worktrees)
              await removeWorktree(repoRoot, w.path, w.branch).catch((cleanupErr) =>
                console.warn(`fan-out ${groupId}: could not remove unused worktree ${w.path}`, cleanupErr),
              );
            throw new TRPCError({
              code: 'INTERNAL_SERVER_ERROR',
              message: 'The race was abandoned: the server is shutting down',
            });
          }

          // The race's description — group, lanes, worktree records — is written in one transaction,
          // before any agent exists. Written row by row alongside the spawning, a failure part-way
          // left a race that was half described: lanes with no worktree record, a group with fewer
          // lanes than worktrees on disk, and a Compare view that could not account for either.
          const planned = worktrees.map((w) => ({
            laneId: nanoid(10),
            runId: nanoid(12),
            index: w.index,
            path: w.path,
            branch: w.branch,
          }));
          try {
            ctx.db.transaction((tx) => {
              repo.createGroup(tx, {
                id: groupId,
                prompt: input.prompt,
                repoRoot,
                baseCommit,
                createdAt: Date.now(),
              });
              for (const lane of planned) {
                repo.createLane(tx, {
                  id: lane.laneId,
                  name: `Agent ${lane.index + 1}`,
                  cwd: lane.path,
                  projectRoot: repoRoot,
                  permission,
                  createdAt: Date.now(),
                  groupId,
                  groupIndex: lane.index,
                });
                repo.insertWorktree(tx, {
                  laneId: lane.laneId,
                  groupId,
                  repoRoot,
                  path: lane.path,
                  branch: lane.branch,
                  baseCommit,
                });
              }
            });
          } catch (err) {
            // Nothing has run yet, so these worktrees are exactly as `git worktree add` left them and
            // removing them destroys no work. This is the only point at which that is true.
            for (const w of worktrees)
              await removeWorktree(repoRoot, w.path, w.branch).catch((cleanupErr) =>
                console.warn(`fan-out ${groupId}: could not remove unused worktree ${w.path}`, cleanupErr),
              );
            throw new TRPCError({
              code: 'INTERNAL_SERVER_ERROR',
              message: `Could not record the race: ${err instanceof Error ? err.message : String(err)}`,
            });
          }

          // Now the agents. From here the worktrees are no longer disposable: an agent that has
          // started may already have written to one, so nothing is deleted if a later start fails.
          // What is owed instead is an honest, durable end state — the ones that started are stopped
          // with the server's own reason, and every lane of the race keeps its failed run to show.
          ctx.hub.publishLanesChanged();
          const started: string[] = [];
          for (const lane of planned) {
            try {
              // Never spawn an agent the server has already decided to stop accepting. The process
              // manager refuses this too, once its own shutdown has begun; this is the earlier,
              // explicit half of the same rule, and it names the reason.
              if (signal.aborted) throw new Error('the server is shutting down');
              await ctx.processes.start({
                runId: lane.runId,
                laneId: lane.laneId,
                prompt: input.prompt,
                cwd: lane.path,
                permission,
                maxTurns: input.maxTurns,
                model: input.model,
                groupId,
              });
              started.push(lane.runId);
            } catch (err) {
              const why = err instanceof Error ? err.message : String(err);
              const reason = `the race could not be started in full: ${why}`;
              for (const runId of started) await ctx.processes.abort(runId, reason);
              throw new TRPCError({ code: 'INTERNAL_SERVER_ERROR', message: reason });
            }
          }
          return {
            groupId,
            baseCommit,
            lanes: planned.map((l) => ({ laneId: l.laneId, runId: l.runId })),
          };
        });
      }),
    get: publicProcedure.input(z.object({ groupId: z.string() })).query(({ ctx, input }) => {
      const g = repo.getGroup(ctx.db, input.groupId);
      if (!g) throw notFound('race');
      return { ...g, lanes: repo.listGroupLanes(ctx.db, g.id) };
    }),
    list: publicProcedure.query(({ ctx }) => repo.listGroups(ctx.db)),
    compare: publicProcedure.input(z.object({ groupId: z.string() })).query(({ ctx, input }) => {
      if (!repo.getGroup(ctx.db, input.groupId)) throw notFound('race');
      // The service coalesces overlapping requests for the same race and bounds how much git runs
      // at once, so several clients watching one race cost one set of diffs.
      return ctx.compare.compare(input.groupId);
    }),
    keep: publicProcedure
      .input(z.object({ groupId: z.string(), runId: z.string().nullable() }))
      .mutation(({ ctx, input }) => {
        if (!repo.getGroup(ctx.db, input.groupId)) throw notFound('race');
        // "Keep this one" means one of this race's own results: a run in one of its lanes that
        // actually executed. A replay or an import in a racing lane is a view of work done
        // elsewhere — keeping one would put a badge on a worktree it never touched.
        if (input.runId !== null) {
          const lanes = new Set(repo.listGroupLanes(ctx.db, input.groupId).map((l) => l.id));
          const run = repo.getRun(ctx.db, input.runId);
          if (!run || !lanes.has(run.laneId) || !repo.isExecution(run))
            throw new TRPCError({
              code: 'BAD_REQUEST',
              message: "That run is not one of this race's results",
            });
          // Keeping is a judgement about a finished piece of work, so the work has to be finished:
          // the latest execution terminal, and the lane not running anything that could still
          // change the worktree under the choice being made.
          const latest = repo.listExecutions(ctx.db, run.laneId).at(-1);
          if (latest && !isTerminal(latest.status))
            throw new TRPCError({
              code: 'PRECONDITION_FAILED',
              message: 'That lane is still running; wait for it to finish before keeping a result',
            });
          assertLaneIdle(ctx, run.laneId);
        }
        repo.setKeptRun(ctx.db, input.groupId, input.runId);
        return { ok: true };
      }),
  }),

  /**
   * Bringing several of a project's sessions together into a new one.
   *
   * The capability the project workspace exists to reach: having watched three sessions work, start
   * a fourth that uses what they produced. What makes it honest rather than magical is that nothing
   * is merged, nothing is sent anywhere, and nothing is inferred — the sources' work is *copied*,
   * once, into a package the console owns, and an ordinary session is started against it.
   */
  wrapups: router({
    /**
     * The sessions an operator is considering, as the server sees them now.
     *
     * The dialog needs three things it cannot work out for itself: the revision each choice will be
     * pinned to, whether the server still owns work in that session, and what its latest execution
     * came to. Asking for them is also what makes the dialog's "you cannot do this yet" and the
     * mutation's refusal the same rule rather than two that drift apart.
     */
    sources: publicProcedure
      .input(z.object({ laneIds: z.array(z.string()).min(1).max(WRAPUP_MAX_SOURCES) }))
      .query(({ ctx, input }) =>
        input.laneIds.map((laneId) => {
          const facts = wrapUpSourceFacts(ctx, laneId);
          return { ...facts, needsAcknowledgement: needsPartialAcknowledgement(facts.outcome) };
        }),
      ),

    /** What went into this session, or `null` when it is not a wrap-up. */
    get: publicProcedure
      .input(z.object({ laneId: z.string() }))
      .query(({ ctx, input }) => repo.getWrapUp(ctx.db, input.laneId) ?? null),

    /**
     * Capture a package from the chosen sessions and start a session against it.
     *
     * The order is the whole design. Validate, capture to a folder this console owns, re-check that
     * the sources did not move while it was being captured, record the wrap-up, and only then start
     * an agent. Every step before the last one is undoable, and each undoes itself: a package with
     * no session is removed, a session with no agent is removed with its package. The first moment
     * anything becomes permanent is the moment an agent could have written something.
     */
    create: publicProcedure
      .input(
        z.object({
          projectRoot: z.string().trim().min(1),
          /** In the order they should appear in the package; each with the revision the operator saw. */
          sources: z
            .array(z.object({ laneId: z.string(), revision: z.string() }))
            .min(1)
            .max(WRAPUP_MAX_SOURCES),
          instructions: z.string().trim().min(1).max(WRAPUP_MAX_INSTRUCTIONS),
          /** Text files chosen from the sources' own folders, by session and relative path. */
          files: z
            .array(z.object({ laneId: z.string(), path: z.string().min(1).max(4096) }))
            .max(WRAPUP_MAX_FILES)
            .default([]),
          acknowledgePartial: z.boolean().default(false),
          name: z.string().min(1).max(80).optional(),
        }),
      )
      .mutation(async ({ ctx, input }) => {
        const laneId = nanoid(10);
        // Inside `track` from the first database read, like `workspaces.create`: this writes a
        // directory and spawns an agent, neither of which may happen after shutdown has taken its
        // inventory of what the server owns.
        return ctx.work.track(`wrap-up ${laneId}`, async (signal) => {
          const projectRoot = projectDir(input.projectRoot);
          const facts = input.sources.map((s) => wrapUpSourceFacts(ctx, s.laneId));
          const decision = planWrapUp(
            {
              projectRoot,
              sources: input.sources,
              files: input.files,
              acknowledgePartial: input.acknowledgePartial,
            },
            facts,
          );
          if (!decision.ok)
            throw new TRPCError({ code: decision.refusal.code, message: decision.refusal.message });
          const { plan } = decision;
          const dir = join(ctx.config.dataDir, 'wrapups', laneId);
          const capturedAt = Date.now();
          let captured: ReturnType<typeof captureWrapUp>;
          try {
            captured = captureWrapUp({
              db: ctx.db,
              wrapUpLaneId: laneId,
              dir,
              projectRoot,
              instructions: input.instructions.trim(),
              partial: plan.partial,
              capturedAt,
              sources: plan.sources.map((f, i) => ({
                // Present: `wrapUpSourceFacts` just read it, inside this same synchronous stretch.
                lane: repo.getLane(ctx.db, f.laneId) as LaneRow,
                executions: repo.listExecutions(ctx.db, f.laneId),
                ordinal: i + 1,
                outcome: f.outcome,
                revision: f.revision,
                files: input.files.filter((x) => x.laneId === f.laneId).map((x) => x.path),
              })),
            });
          } catch (err) {
            throw wrapUpError(err);
          }
          // The sources as they are *after* the capture. Reading events and copying files takes
          // real time, and a package assembled across a change in its own sources would be a
          // snapshot of no single moment. Nothing has run in the folder yet, so saying no is free.
          const abandon = (why: string): never => {
            removeWrapUpPackage(dir);
            throw new TRPCError({ code: 'CONFLICT', message: why });
          };
          if (signal.aborted) abandon('The wrap-up was abandoned: the server is shutting down');
          for (const f of plan.sources) {
            const now = wrapUpSourceFacts(ctx, f.laneId);
            if (now.revision !== f.revision || now.busy)
              abandon(`${f.name} changed while its work was being captured. Try again.`);
          }
          let lane: LaneRow;
          try {
            lane = ctx.db.transaction((tx) => {
              const created = repo.createLane(tx, {
                id: laneId,
                name:
                  input.name?.trim() ||
                  `Wrap-up ${repo.listLanes(ctx.db, { includeArchived: true, projectRoot }).length + 1}`,
                // Its own folder, which is also where the package is: a wrap-up reads its sources
                // and writes somewhere else, and this is the somewhere else.
                cwd: dir,
                // Grouped with the sources it came from, not with the data directory it runs in.
                projectRoot,
                permission: 'allowlist',
                createdAt: capturedAt,
              });
              repo.insertWrapUp(tx, {
                wrapup: {
                  laneId,
                  projectRoot,
                  version: WRAPUP_VERSION,
                  capturedAt,
                  dir,
                  instructions: input.instructions.trim(),
                  partial: plan.partial ? 1 : 0,
                },
                sources: captured.sources.map(({ digest }) => ({
                  wrapLaneId: laneId,
                  sourceLaneId: digest.laneId,
                  ordinal: digest.ordinal,
                  name: digest.name,
                  outcome: digest.outcome,
                  revision: digest.revision,
                })),
                runs: captured.sources.flatMap(({ digest }) =>
                  digest.runs.map((run, i) => ({
                    wrapLaneId: laneId,
                    sourceLaneId: digest.laneId,
                    runId: run.runId,
                    ordinal: i + 1,
                    status: run.status,
                    prompt: run.prompt,
                  })),
                ),
                files: captured.sources.flatMap(({ digest, files }) =>
                  files.map((file) => ({
                    wrapLaneId: laneId,
                    sourceLaneId: digest.laneId,
                    path: file.path,
                    storedPath: file.storedPath,
                    bytes: file.bytes,
                    sha256: file.sha256,
                  })),
                ),
              });
              return created;
            });
          } catch (err) {
            removeWrapUpPackage(dir);
            throw new TRPCError({
              code: 'INTERNAL_SERVER_ERROR',
              message: `Could not record the wrap-up: ${err instanceof Error ? err.message : String(err)}`,
            });
          }
          const runId = nanoid(12);
          try {
            await ctx.processes.start({
              runId,
              laneId,
              // The package's inventory and the operator's brief. No transcript: the documents are
              // on disk for the agent to read, which is the point of capturing them as documents.
              prompt: captured.prompt,
              cwd: dir,
              permission: 'allowlist',
            });
          } catch (err) {
            // The agent never started, so nothing has been written in this folder by anyone but
            // this request. Take the whole thing back rather than leaving a session that cannot be
            // explained beside a package nothing will ever read.
            ctx.db.transaction((tx) => {
              repo.deleteWrapUp(tx, laneId);
              repo.deleteLane(tx, laneId);
            });
            removeWrapUpPackage(dir);
            throw new TRPCError({
              code: 'INTERNAL_SERVER_ERROR',
              message: `The wrap-up could not be started: ${err instanceof Error ? err.message : String(err)}`,
            });
          }
          ctx.hub.publishLanesChanged();
          return { lane: toLaneDto(ctx, lane), runId, dir };
        });
      }),
  }),

  replay: router({
    start: publicProcedure
      .input(
        z.object({
          // The run being replayed, not the run this creates — `replay.start` answers with a new
          // `runId`, and naming both of them `runId` had callers reading their own request back.
          sourceRunId: z.string(),
          speed: z.enum(['instant', '1x', '4x']),
          laneId: z.string().optional(),
        }),
      )
      .mutation(({ ctx, input }) => {
        // Validate the request's subject first, then where it would land.
        const source = repo.getRun(ctx.db, input.sourceRunId);
        if (!source) throw notFound('run');
        // A replay copies the source's outcome onto the new run when it ends. Replaying a run that
        // is still going would copy `running`, leaving a replay nothing can ever finish or cancel.
        if (!isTerminal(source.status))
          throw new TRPCError({
            code: 'PRECONDITION_FAILED',
            message: 'Only finished, failed, or cancelled runs can be replayed',
          });
        // The target lane has to still be there, or the replay would stream into a lane id nothing
        // shows (and `Replayer.start` would happily create the run anyway).
        const laneId = input.laneId ?? source.laneId;
        openLane(ctx, laneId);
        // A playback is an operation in that lane like any other: starting one over a working agent
        // would give the lane two runs claiming to be live, and its Stop would find the wrong one.
        assertLaneIdle(ctx, laneId);
        return {
          runId: ctx.replayer.start(input),
        };
      }),
    import: publicProcedure
      .input(
        z.object({
          laneId: z.string(),
          // Characters, which is what Zod counts — not bytes. A UTF-8 recording of this length is
          // larger on the wire, so this is not a size guarantee. It has been exercised through the
          // real HTTP route with ASCII payloads (2,000,000 accepted in 15 ms, 50,000,000 in 274 ms
          // on the review machine); that measurement is the claim, and nothing here says a browser
          // renders an arbitrarily large history comfortably.
          contents: z.string().min(1).max(50_000_000),
          label: z.string().max(200).optional(),
        }),
      )
      .mutation(({ ctx, input }) => {
        openLane(ctx, input.laneId);
        // Same rule as a replay: a recording dropped into a lane mid-run used to become the newest
        // row, and the composer's Stop went to it instead of to the agent still working.
        assertLaneIdle(ctx, input.laneId);
        return {
          runId: ctx.replayer.import({
            laneId: input.laneId,
            lines: input.contents.split('\n'),
            label: input.label,
          }),
        };
      }),
  }),
});

export type AppRouter = typeof appRouter;
export const createCaller = createCallerFactory(appRouter);
