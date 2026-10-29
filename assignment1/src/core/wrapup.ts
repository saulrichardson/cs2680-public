import type { RunStatus } from './types';

/**
 * Bringing several sessions together into one.
 *
 * A wrap-up is an ordinary new session started from a **package**: a snapshot of what the chosen
 * sessions said and produced, written once to a folder the console owns. Three invariants hold it
 * up, and every rule here keeps one of them:
 *
 * - **A snapshot.** Captured only from sessions that are not running, pinned to the revision they
 *   were at, and never re-read. A source that changes later does not change the package.
 * - **Evidence, not a claim.** Run ids, statuses and prompts as they stood; each copied file with
 *   the SHA-256 of the bytes copied. That says which bytes went in — not that they stay that way.
 * - **Data, not instruction.** A source session's words are untrusted text in a file. Only the
 *   operator's brief instructs.
 *
 * This module is the pure half: vocabulary, bounds, and whether a capture may go ahead. The
 * effects live in `src/server/wrapup/`.
 */

/** The package format, written into every manifest. */
export const WRAPUP_VERSION = 1;

/** How many sessions one wrap-up gathers — the same three the project workspace shows at once. */
export const WRAPUP_MAX_SOURCES = 3;
/** How many files may be carried in, across all sources. */
export const WRAPUP_MAX_FILES = 12;
/** The largest single file that may be carried in. */
export const WRAPUP_MAX_FILE_BYTES = 512 * 1024;
/** The largest a package's copied files may come to in total. */
export const WRAPUP_MAX_TOTAL_BYTES = 4 * 1024 * 1024;
/** How much of one quoted report, brief or reply the package carries. Truncation is recorded. */
export const WRAPUP_MAX_REPORT_CHARS = 20_000;
/**
 * How much quoted text one source may contribute in total.
 *
 * Model output has no fixed length and a session can hold many delegates, so a per-item bound alone
 * still admits an unbounded package. Once this is spent the remaining items are recorded with their
 * full dropped length rather than silently omitted.
 */
export const WRAPUP_MAX_SOURCE_CHARS = 200_000;
/** What the operator may type as the wrap-up's own brief. Matches `runs.start`'s prompt bound. */
export const WRAPUP_MAX_INSTRUCTIONS = 20_000;

/** A piece of recorded text as the package carries it: bounded, and honest about the bound. */
export interface QuotedText {
  text: string;
  /** Characters dropped from the end. Absent when the whole thing is here. */
  dropped?: number;
}

/**
 * Bound one piece of recorded text, keeping what was dropped.
 *
 * `budget` is what is left of the source's total allowance; a budget of zero yields empty text and
 * the full original length as `dropped`, which is what lets the document say "omitted" rather than
 * look like a delegate that wrote nothing.
 */
export function quoteText(raw: string, budget: number): QuotedText {
  const limit = Math.max(0, Math.min(WRAPUP_MAX_REPORT_CHARS, budget));
  if (raw.length <= limit) return { text: raw };
  return { text: raw.slice(0, limit), dropped: raw.length - limit };
}

/** What a source session's latest execution came to, or `none` when it never executed anything. */
export type SourceOutcome = RunStatus | 'none';

/** The part of a run this module reasons about. `RunRow` and `RunSummaryDto` both satisfy it. */
export interface SourceRunFacts {
  id: string;
  status: string;
  startedAt: number;
}

/**
 * A token for "this session, as it stands now".
 *
 * Sent to the client with the dialog's source list and echoed back at capture, so a session that
 * gained a run — or whose run finished differently — between choosing it and confirming is refused
 * rather than captured in a state nobody looked at. Deliberately readable: a token a person can
 * compare in a log is worth more here than a hash, and it is never a security boundary.
 *
 * Executions only. A replay or an import in the session is a view of work done elsewhere; it
 * changes nothing a package would capture, and letting it invalidate a revision would make the
 * dialog go stale for a reason the operator cannot see.
 */
export function sourceRevision(executions: readonly SourceRunFacts[]): string {
  const last = executions.at(-1);
  return `${executions.length}:${last?.id ?? 'none'}:${last?.status ?? 'none'}`;
}

/** What the session's latest execution came to. `none` when it has never executed anything. */
export function sourceOutcome(executions: readonly SourceRunFacts[]): SourceOutcome {
  const last = executions.at(-1);
  if (!last) return 'none';
  const known: ReadonlySet<string> = new Set<RunStatus>(['running', 'finished', 'failed', 'cancelled']);
  // An unrecognised status is not evidence that the session finished, so it is not treated as one.
  return known.has(last.status) ? (last.status as RunStatus) : 'failed';
}

/**
 * Does including this source need the operator to say so?
 *
 * Anything but a finished run: a failed or cancelled session produced partial work, and one that
 * never executed produced none. Bringing either in is a perfectly reasonable thing to want — the
 * partial trajectory is often exactly what the wrap-up is about — but it must be a decision rather
 * than something the operator discovers afterwards in the manifest.
 */
export function needsPartialAcknowledgement(outcome: SourceOutcome): boolean {
  return outcome !== 'finished';
}

/** What the server knows about one candidate source when it decides whether a capture may proceed. */
export interface WrapUpSourceFacts {
  laneId: string;
  name: string;
  projectRoot: string;
  /** See `sourceRevision`. */
  revision: string;
  outcome: SourceOutcome;
  /** The server still owns an execution or a playback for this session (`activeOperation`). */
  busy: boolean;
}

/** What the operator asked for. Shapes the request both the client sends and the router validates. */
export interface WrapUpRequest {
  projectRoot: string;
  /** In the order they should appear in the package; each with the revision the operator saw. */
  sources: readonly { laneId: string; revision: string }[];
  /** Files chosen from those sessions' folders, by source and workspace-relative path. */
  files: readonly { laneId: string; path: string }[];
  acknowledgePartial: boolean;
}

export interface WrapUpPlan {
  /** The chosen sources, in request order, with the facts the capture will record. */
  sources: WrapUpSourceFacts[];
  /** At least one source did not finish, and the operator said to include it anyway. */
  partial: boolean;
}

/** A refusal in the vocabulary the router speaks, so the rule and its HTTP status stay together. */
export interface WrapUpRefusal {
  code: 'BAD_REQUEST' | 'PRECONDITION_FAILED' | 'CONFLICT';
  message: string;
}

export type WrapUpDecision = { ok: true; plan: WrapUpPlan } | { ok: false; refusal: WrapUpRefusal };

/**
 * May this capture go ahead, and with what.
 *
 * Pure, and the single place the rules live: the dialog asks it through the server (so the button
 * it disables and the refusal it would get are the same rule), and the mutation asks it again
 * before touching the filesystem. Order matters — a request naming a session from another project
 * should hear about *that*, not about a revision it could never have had right.
 */
export function planWrapUp(req: WrapUpRequest, facts: readonly WrapUpSourceFacts[]): WrapUpDecision {
  const refuse = (code: WrapUpRefusal['code'], message: string): WrapUpDecision => ({
    ok: false,
    refusal: { code, message },
  });
  if (req.sources.length === 0) return refuse('BAD_REQUEST', 'Choose at least one session to bring together');
  if (req.sources.length > WRAPUP_MAX_SOURCES)
    return refuse('BAD_REQUEST', `A wrap-up brings together at most ${WRAPUP_MAX_SOURCES} sessions`);
  const seen = new Set<string>();
  const chosen: WrapUpSourceFacts[] = [];
  for (const asked of req.sources) {
    if (seen.has(asked.laneId)) return refuse('BAD_REQUEST', 'One session is listed twice');
    seen.add(asked.laneId);
    const found = facts.find((f) => f.laneId === asked.laneId);
    if (!found) return refuse('BAD_REQUEST', 'One of the chosen sessions no longer exists');
    if (found.projectRoot !== req.projectRoot)
      return refuse(
        'BAD_REQUEST',
        `${found.name} belongs to another project. A wrap-up gathers work from one project.`,
      );
    chosen.push(found);
  }
  // Still going. Refused rather than captured mid-flight: a package is a snapshot, and there is no
  // honest snapshot of a session whose next tool call may change the files being copied.
  const busy = chosen.find((f) => f.busy || f.outcome === 'running');
  if (busy)
    return refuse(
      'PRECONDITION_FAILED',
      `${busy.name} is still running. Stop it, or wait for it to finish: a wrap-up captures what a session has produced, and that is still changing.`,
    );
  // Changed under the operator between choosing and confirming.
  const moved = chosen.find((f, i) => f.revision !== req.sources[i]?.revision);
  if (moved)
    return refuse(
      'CONFLICT',
      `${moved.name} has changed since you chose it. Close this and open it again to capture what it says now.`,
    );
  const unfinished = chosen.filter((f) => needsPartialAcknowledgement(f.outcome));
  if (unfinished.length > 0 && !req.acknowledgePartial)
    return refuse(
      'PRECONDITION_FAILED',
      `${unfinished.map((f) => f.name).join(', ')} did not finish, so only partial work would be captured. Confirm that before continuing.`,
    );
  if (req.files.length > WRAPUP_MAX_FILES)
    return refuse('BAD_REQUEST', `A wrap-up carries at most ${WRAPUP_MAX_FILES} files`);
  const stray = req.files.find((f) => !seen.has(f.laneId));
  if (stray)
    return refuse('BAD_REQUEST', 'A chosen file belongs to a session that is not one of the sources');
  return { ok: true, plan: { sources: chosen, partial: unfinished.length > 0 } };
}

// ---------- what a captured package says about itself ----------
// The wire shapes `wrapups.get` answers with, and the shapes the on-disk manifest is written in.
// They are the same types on purpose: the database is the index and the manifest is the copy that
// travels with the files, and a reader of either should be looking at the same facts.

/** One execution of a source session, as it stood when the package was captured. */
export interface CapturedRun {
  runId: string;
  status: RunStatus;
  prompt: string;
}

/** One file copied into the package, and the bytes it was copied from. */
export interface CapturedFile {
  /** Where it came from, relative to the source session's own folder. */
  path: string;
  /** Where it now is, relative to the package directory. */
  storedPath: string;
  bytes: number;
  /** SHA-256 of the copied bytes. Provenance: which bytes went in, not a promise about the future. */
  sha256: string;
}

export interface CapturedSource {
  laneId: string;
  /** Position in the package, from 1. What the `sources/NN-…` folders are numbered by. */
  ordinal: number;
  /** The session's name when it was captured. It may have been renamed since. */
  name: string;
  outcome: SourceOutcome;
  /** The revision the capture was made against (see `sourceRevision`). */
  revision: string;
  runs: CapturedRun[];
  files: CapturedFile[];
  /**
   * The session this came from is still in this console, so the wrap-up can link to it.
   *
   * A wrap-up is not invalidated by losing sight of a source — the package is the record, and it is
   * complete on its own — so the link is dropped rather than the source, and the view says the
   * session is no longer here.
   */
  present: boolean;
}

export interface WrapUpDto {
  /** The wrap-up session itself. */
  laneId: string;
  projectRoot: string;
  version: number;
  capturedAt: number;
  /** The package directory, which is also the wrap-up session's working folder. */
  dir: string;
  /** What the operator asked the wrap-up to do. */
  instructions: string;
  /** A source that did not finish was included, with the operator's acknowledgement. */
  partial: boolean;
  sources: CapturedSource[];
}

/** The number a source's folder is named with: `01`, `02`, … so the package sorts in order. */
export function sourceFolderPrefix(ordinal: number): string {
  return String(ordinal).padStart(2, '0');
}

/**
 * A folder-safe stem for a session's name, so a package is readable on disk.
 *
 * Never load-bearing: the ordinal is what makes the folder unique, and an empty stem is dropped
 * rather than substituted, so a session named entirely in a script this rule strips does not end up
 * with a folder called `untitled`.
 */
export function sourceSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 24)
    .replace(/-+$/, '');
}
