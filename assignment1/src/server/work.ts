/**
 * The effectful work this server is in the middle of doing on behalf of a request.
 *
 * A request is not the same thing as the work it started. Preparing a race writes worktrees to disk
 * and rows to the database, and it goes on doing that whether or not the browser is still listening
 * — a reload, a closed tab or a dropped connection ends the *response*, not the `git worktree add`.
 * Shutdown counted responses, so an aborted client made the server believe it owned nothing and
 * close SQLite underneath work that was still running.
 *
 * So ownership is tracked by the work's own promise, from the moment it starts until it settles.
 * Three things follow, and they are the whole point of this module:
 *
 * - shutdown can wait for what it actually owns, and say what it was if the wait runs out;
 * - once shutdown has begun nothing new is admitted, which is what makes that wait finite;
 * - work that knows how to stop is *told* to, rather than waited out. Preparing a race is several
 *   sequential git commands, each with a budget of its own, so waiting alone could easily outlast
 *   any sensible shutdown while a command this server knows how to abandon was still running.
 *
 * Deliberately not a job framework: no queue, no retries, no persistence, no scheduling. The work
 * here is already running; this only remembers whose it is and how to call it off.
 */
export class OwnedWork {
  private readonly live = new Map<number, { label: string; settled: Promise<void> }>();
  private nextId = 1;
  private closing = false;
  private readonly cancellation = new AbortController();

  /**
   * Run `task`, and own it until it settles.
   *
   * The label is what shutdown will report if this is still running when the budget runs out, so it
   * should name the work and the thing it is working on ("fan-out abc123"), not the endpoint.
   *
   * The task is handed a signal that is aborted when shutdown begins. Honouring it is the task's
   * own business — it is the only thing that knows which of its steps may be abandoned and what it
   * owes afterwards (a half-prepared race still has to be rolled back, and a worktree an agent may
   * have touched still has to be kept).
   */
  track<T>(label: string, task: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (this.closing) return Promise.reject(new Error(`${label} was refused: the server is shutting down`));
    let running: Promise<T>;
    try {
      running = task(this.cancellation.signal);
    } catch (err) {
      // A task that throws before its first await never ran; there is nothing to own.
      return Promise.reject(err);
    }
    const id = this.nextId;
    this.nextId += 1;
    // The tracked copy swallows the outcome: it exists to be waited on by shutdown, and the real
    // result — including a rejection — belongs to the caller, who is returned the original promise.
    const settled = running.then(
      () => {
        this.live.delete(id);
      },
      () => {
        this.live.delete(id);
      },
    );
    this.live.set(id, { label, settled });
    return running;
  }

  /** What is running right now, by label. */
  pending(): string[] {
    return [...this.live.values()].map((w) => w.label);
  }

  /**
   * Close admission, call off what is already running, and wait for it to settle.
   *
   * Answers with the labels of whatever was *still* running when the budget ran out — empty means
   * the server owns nothing more and its database can be closed. A non-empty answer is a shutdown
   * failure to report, not a warning to walk past.
   */
  async settle(budgetMs: number): Promise<string[]> {
    this.closing = true;
    this.cancellation.abort(new Error('the server is shutting down'));
    const deadline = Date.now() + budgetMs;
    while (this.live.size > 0 && Date.now() < deadline) {
      await Promise.race([
        Promise.all([...this.live.values()].map((w) => w.settled)),
        new Promise((resolve) => setTimeout(resolve, 20)),
      ]);
    }
    return this.pending();
  }
}
