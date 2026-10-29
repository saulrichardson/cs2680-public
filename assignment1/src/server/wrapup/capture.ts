import { createHash } from 'node:crypto';
import { mkdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import type { CapturedFile, SourceOutcome } from '@/core/wrapup';
import { WRAPUP_MAX_FILE_BYTES, WRAPUP_MAX_TOTAL_BYTES } from '@/core/wrapup';
import type { Db } from '@/server/db';
import type { LaneRow, RunRow } from '@/server/db/schema';
import { previewKind, readBoundedWorkspaceFile, WorkspaceError } from '@/server/workspace/files';
import {
  digestSource,
  type Manifest,
  renderManifest,
  renderSourceDocument,
  type SourceDigest,
  sourceDocumentPath,
  sourceFolder,
  wrapUpPrompt,
} from './package';

/**
 * Writing a wrap-up's package to disk. Two boundaries, neither of them new:
 *
 * - **Reading** goes through `readBoundedWorkspaceFile`: proven inside the source session's own
 *   folder, opened `O_NOFOLLOW`, and measured and read from one descriptor.
 * - **Writing** only ever happens under the package directory, which this creates inside the
 *   console's data directory. Nothing here writes to a source session's folder.
 *
 * On any failure the directory this call created is removed — safe because nothing has run in it
 * yet, the same moment `workspaces.create` relies on to roll back a checkout.
 */

/** A refusal the router turns into a status, keeping the reason it gave. */
export class WrapUpError extends Error {
  constructor(
    message: string,
    readonly code: 'BAD_REQUEST' | 'INTERNAL_SERVER_ERROR' = 'BAD_REQUEST',
  ) {
    super(message);
    this.name = 'WrapUpError';
  }
}

/** Everything one source contributes to a capture, resolved by the router before it calls here. */
export interface CaptureSource {
  lane: LaneRow;
  /** The session's executions, oldest first. Replays and imports are not captured. */
  executions: RunRow[];
  ordinal: number;
  outcome: SourceOutcome;
  revision: string;
  /** Files the operator chose, as workspace-relative paths in this session's own folder. */
  files: readonly string[];
}

export interface CaptureInput {
  db: Db;
  wrapUpLaneId: string;
  /** The package directory, which becomes the wrap-up session's working folder. Must not exist. */
  dir: string;
  projectRoot: string;
  instructions: string;
  partial: boolean;
  capturedAt: number;
  sources: readonly CaptureSource[];
}

export interface CaptureResult {
  manifest: Manifest;
  /** The wrap-up session's first message. Points at the package; carries none of its content. */
  prompt: string;
  sources: { digest: SourceDigest; files: CapturedFile[] }[];
}

/** Where the package's documents and copied files live, inside the wrap-up's own folder. */
export const INPUTS_DIR = 'inputs';

/**
 * Capture the package. Creates `dir`, fills it, and answers with what it recorded.
 *
 * The directory must not already exist: a wrap-up owns its folder outright, and quietly adopting
 * one that is already there would mean writing a package beside somebody else's files and then
 * pointing an agent at the result.
 */
export function captureWrapUp(input: CaptureInput): CaptureResult {
  mkdirSync(dirname(input.dir), { recursive: true });
  try {
    // Not `recursive`: EEXIST is the answer we want if something is already there.
    mkdirSync(input.dir);
  } catch (err) {
    throw new WrapUpError(
      `Could not create the wrap-up folder ${input.dir}: ${err instanceof Error ? err.message : String(err)}`,
      'INTERNAL_SERVER_ERROR',
    );
  }
  try {
    const inputs = join(input.dir, INPUTS_DIR);
    mkdirSync(inputs, { recursive: true });
    let totalBytes = 0;
    const captured: { digest: SourceDigest; files: CapturedFile[] }[] = [];
    for (const source of input.sources) {
      const digest = digestSource(input.db, source.lane, source.executions, {
        ordinal: source.ordinal,
        outcome: source.outcome,
        revision: source.revision,
      });
      const folder = sourceFolder(digest);
      mkdirSync(join(inputs, folder), { recursive: true });
      const files: CapturedFile[] = [];
      const copied = new Set<string>();
      for (const rel of source.files) {
        const file = copyIntoPackage({
          sourceRoot: source.lane.cwd,
          sourceName: source.lane.name,
          rel,
          packageRoot: inputs,
          folder,
          budgetLeft: WRAPUP_MAX_TOTAL_BYTES - totalBytes,
          copied,
        });
        totalBytes += file.bytes;
        files.push(file);
      }
      writeFileSync(
        join(inputs, sourceDocumentPath(digest)),
        renderSourceDocument(digest, input.capturedAt),
        'utf8',
      );
      captured.push({ digest, files });
    }
    const manifest = renderManifest({
      wrapUpLaneId: input.wrapUpLaneId,
      projectRoot: input.projectRoot,
      instructions: input.instructions,
      partial: input.partial,
      capturedAt: input.capturedAt,
      sources: captured,
    });
    writeFileSync(join(inputs, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    return {
      manifest,
      prompt: wrapUpPrompt({
        instructions: input.instructions,
        partial: input.partial,
        sources: captured,
      }),
      sources: captured,
    };
  } catch (err) {
    // Nothing has run in this folder, so removing it destroys no work — and leaving a half-written
    // package behind would leave an operator with a directory that claims to be a record of
    // something and is not.
    removeWrapUpPackage(input.dir);
    throw err;
  }
}

/**
 * Remove a package directory this console created and never handed to an agent.
 *
 * Used by `captureWrapUp`'s own failure path and by the router when recording the wrap-up fails
 * afterwards. Never used to tidy up a wrap-up that has run: that is history, and this console does
 * not delete history.
 */
export function removeWrapUpPackage(dir: string): void {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch (err) {
    console.warn(`wrap-up: could not remove the unused package at ${dir}`, err);
  }
}

/**
 * Copy one chosen file into the package, and say which bytes went in.
 *
 * Text only: the package exists to be read by a model, so bytes it cannot read are refused by name
 * rather than copied. The read itself is bounded at the open descriptor
 * (`readBoundedWorkspaceFile`), so nothing between deciding a file is small enough and copying it
 * can substitute a different or larger one.
 */
function copyIntoPackage(args: {
  sourceRoot: string;
  sourceName: string;
  rel: string;
  packageRoot: string;
  folder: string;
  budgetLeft: number;
  copied: Set<string>;
}): CapturedFile {
  // By name, before the file is opened: an image or an archive is not something to spend a read on.
  const kind = previewKind(basename(args.rel));
  if (kind !== 'text' && kind !== 'markdown')
    throw new WrapUpError(
      `${args.sourceName}: ${args.rel} is not a text file, so it cannot be carried into a wrap-up. Its session's folder still has it.`,
    );
  let read: { path: string; bytes: Buffer };
  try {
    read = readBoundedWorkspaceFile(args.sourceRoot, args.rel, WRAPUP_MAX_FILE_BYTES);
  } catch (err) {
    if (err instanceof WorkspaceError)
      throw new WrapUpError(`${args.sourceName}: ${args.rel} cannot be read — ${err.message}`);
    throw err;
  }
  const bytes = read.bytes;
  if (bytes.byteLength > args.budgetLeft)
    throw new WrapUpError(
      `Together the chosen files are over the ${WRAPUP_MAX_TOTAL_BYTES}-byte limit for one wrap-up. Choose fewer.`,
    );
  // The resolved path, not the requested one: `a/../b.md` and `b.md` name one file and must not
  // become two entries, and the recorded path is then the one the manifest can be checked against.
  const normalized = posix(relative(realpathSync(args.sourceRoot), read.path));
  if (args.copied.has(normalized))
    throw new WrapUpError(`${args.sourceName}: ${args.rel} is already included in this package`);
  args.copied.add(normalized);
  const storedPath = `${args.folder}/files/${normalized}`;
  const dest = resolve(args.packageRoot, storedPath);
  const filesRoot = resolve(args.packageRoot, args.folder, 'files');
  // `relative` above cannot produce an escape, but the destination is where an escape would do
  // damage, so it is checked where it matters rather than inferred from the check before it.
  if (dest !== filesRoot && !dest.startsWith(filesRoot + sep))
    throw new WrapUpError(`${args.sourceName}: ${args.rel} cannot be stored under that name`);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, bytes);
  return {
    path: normalized,
    storedPath,
    bytes: bytes.byteLength,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  };
}

/** A relative path with `/` separators, so a manifest reads the same on every platform. */
function posix(p: string): string {
  return sep === '/' ? p : p.split(sep).join('/');
}
