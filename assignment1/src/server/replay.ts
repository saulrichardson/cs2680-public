import { nanoid } from 'nanoid';
import { applyEnvelopes, createRunView } from '@/core/reducer';
import { parseLine } from '@/core/schemas';
import { isTerminal } from '@/core/status';
import type { Envelope, RawEvent } from '@/core/types';
import type { Db } from '@/server/db';
import * as repo from '@/server/db/repo';
import type { HubLike } from '@/server/ws/hub';

export type ReplaySpeed = 'instant' | '1x' | '4x';

const messageOf = (err: unknown) => (err instanceof Error ? err.message : String(err));

interface LiveReplay {
  timer: NodeJS.Timeout | null;
  cancelled: boolean;
  laneId: string;
  /**
   * The stamp of the last envelope actually emitted, on this run's own clock.
   *
   * A playback re-bases the recording's timing on the new run's start, so at `4x` the events it has
   * already sent carry stamps *ahead of the wall clock* — a quarter of the way through a 80 s
   * recording, 20 s of run time has been emitted in 5 s. Ending such a run at `now` would put its
   * own bars outside its axis. Whatever ends a playback ends it no earlier than what it has already
   * said happened, and no later than the recording it stopped part-way through.
   */
  lastStamp: number;
}

export class Replayer {
  private readonly live = new Map<string, LiveReplay>();
  private readonly now: () => number;

  constructor(private readonly deps: { db: Db; hub: HubLike; now?: () => number }) {
    this.now = deps.now ?? (() => Date.now());
  }

  isLive(runId: string): boolean {
    return this.live.has(runId);
  }

  /**
   * Play a recording back as a new run of this lane.
   *
   * The ownership rule, which every path below obeys: from the moment the run row exists, this
   * playback is *owned* — it is in `live`, Stop can reach it, the lane counts it as busy — and the
   * only thing that releases it is a terminal state being written down. Not the last event, not the
   * timer chain ending, and not a write failing part-way through.
   *
   * That rule is the fix for a family of failures that looked unrelated: a rejected terminal UPDATE
   * escaped a `setTimeout` as an uncaught exception (taking the server, and every live agent, with
   * it) while the row stayed `running` and nothing owned it; an instant playback whose events could
   * not be written threw out of `start` and left the same orphan behind. There is now one way out,
   * and it always decides an outcome.
   */
  start(opts: { sourceRunId: string; speed: ReplaySpeed; laneId?: string }): string {
    const source = repo.getRun(this.deps.db, opts.sourceRunId);
    if (!source) throw new Error(`unknown run ${opts.sourceRunId}`);
    const laneId = opts.laneId ?? source.laneId;
    const runId = nanoid(12);
    const startedAt = this.now();
    // Before this line there is nothing to own, so a failure here is simply thrown to the caller.
    repo.createRun(this.deps.db, {
      id: runId,
      laneId,
      prompt: source.prompt,
      effectiveCwd: source.effectiveCwd,
      permission: source.permission,
      // The recording's own browser setting, copied like the prompt and the permission rather than
      // left to `createRun`'s `off` default. It is not a setting this playback obeys — nothing is
      // spawned — it is what the recorded run *asked for*, and browser evidence is derived from
      // the request together with the events. Dropping it turned a recorded configuration failure
      // (Chrome requested, no browser tools in `init`, so `unavailable`) into `off`, which reads as
      // "this session never wanted the browser" — the one thing the run proved was untrue.
      browser: source.browser,
      origin: 'replay',
      status: 'running',
      startedAt,
      replayOf: source.id,
      model: source.model ?? undefined,
    });
    // After it, the run is owned whatever its speed — an instant playback included. It used to be
    // registered only for paced speeds, which is why an instant one that failed had no owner.
    const state: LiveReplay = { timer: null, cancelled: false, laneId, lastStamp: startedAt };
    this.live.set(runId, state);
    this.deps.hub.publishLifecycle({ laneId, runId, origin: 'replay', status: 'running', startedAt });

    let rows: ReturnType<typeof repo.listEvents>;
    try {
      rows = repo.listEvents(this.deps.db, source.id);
    } catch (err) {
      this.end(runId, 'failed', `could not read the recording: ${messageOf(err)}`);
      return runId;
    }
    // One clock per run (see `eventTime` in src/core/reducer.ts): every replayed envelope is stamped
    // at this run's start plus its offset within the recording, so the replay keeps the recording's
    // relative timing on the new run's own axis in `instant`, `1x` and `4x` alike. Only the stamp
    // changes — `1x`/`4x` pacing still follows the recorded gaps in real time. Consequence: a
    // replayed run reports the recording's durations and wall time (an instant replay of an 82 s run
    // shows 82 s), and while a `1x`/`4x` playback is in progress its already-emitted events carry
    // `receivedAt` values ahead of the wall clock.
    // `listEvents` orders by `seq`, and both writers keep `receivedAt` non-decreasing with `seq`
    // (`manager.ts` stamps a monotonic `now()`, `import()` below takes a `Math.max`), so the first
    // row by seq is also the earliest stamp and is the right base for the offsets.
    const base = rows[0]?.receivedAt ?? startedAt;
    const stampOf = (row: { receivedAt: number }) => startedAt + (row.receivedAt - base);
    /** Emit one recorded event as this run's; `false` means the playback has already been ended. */
    const play = (index: number): boolean => {
      const row = rows[index];
      if (!row) return false;
      const stamp = stampOf(row);
      try {
        this.emit(runId, laneId, index + 1, stamp, repo.eventOf(row), row.json);
      } catch (err) {
        this.end(runId, 'failed', `could not persist replay events: ${messageOf(err)}`);
        return false;
      }
      state.lastStamp = Math.max(state.lastStamp, stamp);
      return true;
    };

    const finish = () => {
      // the recording's own duration, on this run's clock; a source still running (or whose run row
      // predates its own events) falls back to the last anchored stamp rather than the wall clock,
      // which for an instant replay would report a run of no duration at all
      const lastRow = rows.at(-1);
      const recorded =
        source.endedAt != null && source.startedAt != null
          ? startedAt + (source.endedAt - source.startedAt)
          : lastRow
            ? stampOf(lastRow)
            : this.now();
      // A replay must always end in a terminal state: copying a still-running source's `running`
      // onto it would leave a run nothing can finish or cancel. `replay.start` refuses such a
      // source up front; this is the last line of defence for any other caller.
      const terminal = isTerminal(source.status);
      const written = this.persistTerminal(runId, {
        status: terminal ? source.status : 'failed',
        // never earlier than what this run has already said happened (see `LiveReplay.lastStamp`)
        endedAt: Math.max(recorded, state.lastStamp),
        sessionId: source.sessionId,
        costUsd: source.costUsd,
        durationMs: source.durationMs,
        durationApiMs: source.durationApiMs,
        numTurns: source.numTurns,
        errorMessage: terminal ? source.errorMessage : 'source run had not finished',
        exitCode: source.exitCode,
      });
      // The recording played, but its ending could not be recorded. The run is still owned at this
      // point — `persistTerminal` only releases ownership once the outcome is durable — so it can
      // still be ended the one way that is left.
      if (!written) this.end(runId, 'failed', 'the playback finished but its outcome could not be recorded');
    };

    if (opts.speed === 'instant' || rows.length === 0) {
      for (let i = 0; i < rows.length; i += 1) if (!play(i)) return runId;
      finish();
      return runId;
    }

    const factor = opts.speed === '4x' ? 4 : 1;
    let i = 0;
    // A timer callback has no caller to reject to: an exception escaping here would be an uncaught
    // exception, which takes the server and every live agent with it. Every failure inside `play`
    // and `finish` is therefore an outcome, not a throw.
    const step = () => {
      if (state.cancelled) return;
      if (i >= rows.length) {
        finish();
        return;
      }
      const row = rows[i];
      if (!play(i)) return;
      i += 1;
      const next = rows[i];
      if (!next || !row) {
        finish();
        return;
      }
      const gap = Math.max(0, Math.min(next.receivedAt - row.receivedAt, 30_000)) / factor;
      state.timer = setTimeout(step, gap);
    };
    step();
    return runId;
  }

  cancel(runId: string): boolean {
    return this.end(runId, 'cancelled');
  }

  /**
   * End a playback in flight: stop its timers, record a terminal state, and say so.
   *
   * One path for every way a playback stops — the operator's Stop, a shutdown, a write that failed —
   * so none of them can leave a timer armed against a database that is closing, or a run row stuck
   * at `running` in a lane that will never free.
   */
  private end(runId: string, status: 'cancelled' | 'failed', errorMessage?: string): boolean {
    const state = this.live.get(runId);
    if (!state) return false;
    // No earlier than what has already been emitted (see `LiveReplay.lastStamp`).
    const endedAt = Math.max(this.now(), state.lastStamp);
    if (this.persistTerminal(runId, { status, endedAt, errorMessage })) return true;
    // The database cannot even be told how this ended. Its timers are stopped and its outcome is
    // reported to whoever is watching; keeping it "live" would only mean a lane nothing can free.
    console.error(
      `replay ${runId}: ended ${status}${errorMessage ? ` (${errorMessage})` : ''} but this could not be recorded`,
    );
    this.live.delete(runId);
    this.deps.hub.publishLifecycle({
      laneId: state.laneId,
      runId,
      origin: 'replay',
      status,
      startedAt: endedAt,
      endedAt,
      error: { message: errorMessage ?? 'its outcome could not be recorded' },
    });
    return true;
  }

  /**
   * Write a playback's terminal state, and release ownership only if that write lands.
   *
   * The order is the point. Releasing first and persisting afterwards is what left a run marked
   * `running` in the database with nothing live to finish it, when the `UPDATE` was rejected — and,
   * because this runs inside a timer, threw the rejection at the process instead of at a caller.
   */
  private persistTerminal(
    runId: string,
    patch: Parameters<typeof repo.updateRun>[2] & { status: string },
  ): boolean {
    const state = this.live.get(runId);
    if (!state) return false;
    // Whatever happens to the write, no further event of this playback is emitted.
    state.cancelled = true;
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    try {
      repo.updateRun(this.deps.db, runId, patch);
    } catch (err) {
      console.error(`replay ${runId}: could not record its ${patch.status} state`, err);
      return false;
    }
    this.live.delete(runId);
    const run = repo.getRun(this.deps.db, runId);
    if (run) this.deps.hub.publishLifecycle(repo.lifecycleOf(run));
    return true;
  }

  import(opts: { laneId: string; lines: string[]; label?: string }): string {
    const runId = nanoid(12);
    const startedAt = this.now();
    const lane = repo.getLane(this.deps.db, opts.laneId);
    const envelopes: Envelope[] = [];
    const jsons: string[] = [];
    // One clock per run (see `eventTime` in src/core/reducer.ts), same rule as a replay: the
    // recording's relative timing anchored at the moment of import, so a recording older than the
    // wall clock keeps its durations instead of collapsing onto `now`, and the run is not back-dated
    // (lanes order runs by `startedAt`). A line with no parseable timestamp — an unparsed line, a
    // `system`/`result` event — carries the previous line's stamp, and the guard keeps `receivedAt`
    // non-decreasing across a backwards jump in the recording.
    // Forward gaps are deliberately uncapped here, so the imported timeline is a faithful picture of
    // the recording; `start()`'s 30 s cap is on *playback* waiting, not on the stamps it writes.
    let firstTs: number | undefined;
    let lastTs = startedAt;
    opts.lines.forEach((line) => {
      if (line.trim().length === 0) return;
      const parsed = parseLine(line);
      const event: RawEvent = parsed.ok
        ? parsed.event
        : { type: 'unparsed', raw: parsed.raw, error: parsed.error };
      const ts = typeof event.timestamp === 'string' ? Date.parse(event.timestamp) : Number.NaN;
      if (!Number.isNaN(ts)) {
        if (firstTs === undefined) firstTs = ts;
        lastTs = Math.max(lastTs, startedAt + (ts - firstTs));
      }
      envelopes.push({ laneId: opts.laneId, runId, seq: envelopes.length + 1, receivedAt: lastTs, event });
      // the imported file's own text, malformed lines included, so an export of this run is a
      // faithful copy of what was imported (see `repo.eventOf` for the read side)
      jsons.push(line);
    });
    const init = envelopes.find((e) => e.event.type === 'system' && e.event.subtype === 'init')?.event;
    const cwd = (typeof init?.cwd === 'string' ? init.cwd : undefined) ?? lane?.cwd ?? '/';
    const view = applyEnvelopes(
      createRunView({
        runId,
        laneId: opts.laneId,
        prompt: `Imported: ${opts.label ?? 'events.jsonl'}`,
        cwd,
        startedAt,
        origin: 'import',
      }),
      envelopes,
    );
    // The reducer never sets a run's status — that is the server's call, and for an import the
    // server is this method: the recording's own `result` event decides, and a stream without one
    // is a failure.
    const status = view.outcome ? (view.outcome.isError ? 'failed' : 'finished') : 'failed';

    // One transaction for the whole recording. An import is not a process to watch: every row is
    // known before the first write, so a partial one has no reason to exist. Writing the run row
    // first and its events afterwards meant a failure part-way through (a full disk, a lock) left a
    // row stuck at `running` in a lane nothing would ever finish, holding that lane's one-operation
    // slot, with half a recording under it. Either the whole recording lands or none of it does.
    this.deps.db.transaction((tx) => {
      repo.createRun(tx, {
        id: runId,
        laneId: opts.laneId,
        prompt: view.prompt,
        effectiveCwd: cwd,
        permission: 'allowlist',
        origin: 'import',
        status,
        startedAt,
        // the run ends after its last event, so it has a real wall time and its bars stay inside the axis
        endedAt: lastTs,
        // Deliberately no `session_id`: the recording's session belongs to whoever recorded it, and
        // a follow-up in this lane that resumed it would hand `--resume` an id this machine's CLI
        // never issued (QA: `--resume requires a valid session ID … "s-import-1" is not a UUID`).
        // The id stays in the imported init event, so the run's own view still shows it. `origin`
        // is what actually keeps it out of `repo.isResumeCandidate`; this only avoids storing a
        // session that is not ours to hand back.
        sessionId: null,
        model: view.model ?? undefined,
        costUsd: view.numbers?.costUsd,
        durationMs: view.numbers?.durationMs,
        durationApiMs: view.numbers?.durationApiMs,
        numTurns: view.numbers?.numTurns,
        errorMessage:
          view.error?.message ?? (view.outcome ? undefined : 'imported stream has no result event'),
      });
      envelopes.forEach((env, i) => {
        repo.insertEvent(tx, {
          runId,
          seq: env.seq,
          receivedAt: env.receivedAt,
          type: env.event.type,
          parentToolUseId:
            typeof env.event.parent_tool_use_id === 'string' ? env.event.parent_tool_use_id : null,
          // the imported file's own text, malformed lines included, so an export of this run is a
          // faithful copy of what was imported (see `repo.eventOf` for the read side)
          json: jsons[i] as string,
        });
      });
    });

    // Broadcast only after the transaction has committed: a client must never be shown a run the
    // database does not have. The run is announced in the state it is already in — an import is
    // instantaneous, and publishing `running` first would be a lifecycle nothing ever lived.
    const run = repo.getRun(this.deps.db, runId);
    if (run) this.deps.hub.publishLifecycle(repo.lifecycleOf(run));
    for (const env of envelopes) this.deps.hub.publishEvent(env);
    return runId;
  }

  /**
   * Stop every playback in flight. The server owns the database, and a `1x`/`4x` replay is a chain
   * of timers that would otherwise fire against a closed connection after `sqlite.close()` — so
   * shutdown ends them the same way a user's Stop does, through the one bounded cancel path, while
   * the database is still open to record it.
   */
  shutdown(): void {
    for (const runId of [...this.live.keys()]) this.cancel(runId);
  }

  private emit(
    runId: string,
    laneId: string,
    seq: number,
    receivedAt: number,
    event: RawEvent,
    json: string,
  ): void {
    repo.insertEvent(this.deps.db, {
      runId,
      seq,
      receivedAt,
      type: event.type,
      parentToolUseId: typeof event.parent_tool_use_id === 'string' ? event.parent_tool_use_id : null,
      json,
    });
    this.deps.hub.publishEvent({ laneId, runId, seq, receivedAt, event });
  }
}
