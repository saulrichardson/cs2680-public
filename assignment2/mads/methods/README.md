# Worked investigation methods — optional, runnable, task-independent

These are **worked examples**, not another required checklist or an answer bank.
The small programs are deliberately incomplete or defective. Each demonstration
runs their actual source, records a discriminating failure, edits only an owned
temporary copy, and checks the explanation again. No task repository is touched.
Choose, inspect, adapt, extend or ignore them. Python's standard library suffices:

```sh
python /absolute/harness/mads/methods/run.py consumer
python /absolute/harness/mads/methods/run.py relations
```

`all` runs both. Use the absolute `method_library` path in ACTUAL RUN PATHS to
locate this directory. Ordinary `read`/`bash` are sufficient. Scripts are readable;
there is no hidden test generator or stronger-model service behind them.

## Consumer flow: a correct helper can leave the requested feature absent

Toy request: “The invoice command subtracts `--discount-cents` from the invoice,
floored at zero; with no discount preserve current output.” Its named interface
is `total_cents(amounts, discount_cents=0)`.

1. The new helper returns **270** for `[100, 200]` and discount `30`. A helper test
   passes. The executable command returns **300**. These are different claims.
2. Read the command's data flow: parser → `args.discount_cents` → old inline sum →
   printed output. The new function has no call on this path. Neither a supplied
   function name nor a plausible tiny reference patch narrows the behavior request.
3. The decisive change is wiring the real consumer, not adding another helper
   assertion. Afterward the **same subprocess invocation** returns **270**; no
   discount still returns **300**, and an excessive discount produces **0**.

See `examples/invoice.py` and `run.py:consumer`. In an unfamiliar repository, find
the equivalent public entrypoint and observe it. A CLI invocation, real request
handler, package export, event callback or configuration lifecycle may be the
contract's entrypoint. Search definitions **and uses**; follow the value into the
observable effect. Dependency APIs and cleanup paths can be part of that flow.
An intended rejection should be checked as a rejection, not forced to “success”.
Do not copy this toy patch into another domain: the task defines the flow to trace.

## Legal families and independent oracles: attack the shared assumption

Toy contract: count the union of finite integer intervals `[start, stop)`.
Half-open intervals include start and exclude stop; zero-width intervals are legal.
The initial implementation passes the displayed disjoint/overlap examples.

`relations` enumerates a finite endpoint domain and compares the real function to
an independently expressed oracle: a set of the covered integer units. It finds
and reduces a counterexample. A contained interval can shrink the accumulated
right boundary; another “ordinary overlap” example did not distinguish that
wrong abstraction. Changing the maintained invariant fixes the **family**, then
reordering, duplicating, translating and splitting interval representations
challenge the repair. These relations derive from union semantics, not from what
the implementation currently happens to return.

Transfer the *method*, not the intervals: identify a legal change of representation
or operation sequence that should preserve an observable result, and exercise the
real API. Consult the installed dependency's contract before labelling an unusual
but legal value or sequence “invalid”. Separate a finite test domain from a proof
about all inputs. If the first failure is big, reduce it while preserving domain
legality and the same failure mechanism; print enough input to reproduce it.

The final check deliberately proposes the **wrong oracle**: length of `[2,2)` is
one. The public function returns zero, correctly. The half-open contract and
literal membership settle this; changing production to satisfy that assertion
would create a bug. Revisions to tests need contract evidence, not a wish to turn
a red run green. Preserve the old expectation, contrary evidence and why it was
revised. When original statements conflict, retain both and explain a reasonable
interpretation instead of inventing certainty.

## Applying an example without turning it into a ritual

A short continuation note can preserve the useful hypothesis rather than every
past guess: “observable claim; real entrypoint; assumption; discriminating input;
observed result; alternative explanation; next action.” Keep it if helpful, not
as a required form. `verification` can store executable intentions; `task` can
link fallible claims to exact original spans. Both tools are optional. The final
reviewer's agreement, a generated oracle, and this library are themselves
fallible—run a discriminating check.

These examples were investigator-authored as general development material after
observing scope-narrowing and example-fitting in several repositories. They
contain no assignment instance IDs, repository patches, hidden tests or answers.
Their own passing demonstration is not evidence that a solver used the method or
that it improved task completion. That requires separate, attributable trials.
