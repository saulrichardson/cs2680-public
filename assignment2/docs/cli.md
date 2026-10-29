# CLI and run artifacts

Use `python -m mads --help` from the harness checkout. This is a stdlib dispatcher
over existing implementations, not a separate runtime or installed CLI framework.
Each subcommand supports `--help`. Existing entrypoints continue to work.

| Command | Purpose |
| --- | --- |
| `run REPO --task FILE --run-dir NEW_DIR` | Shared agent, default Course route |
| `assignment run INDEX [--run-root DIR]` | Container setup, run, cleanup and patch export |
| `assignment export REPO BASE RUN` | Export only with established cleanup evidence |
| `logs RUN --follow` | Live journal prefix with record references |
| `logs RUN --event ID --raw` | Exact captured payload or serialized record |
| `inspect RUN summary` | Recorded counts, outcomes, context and usage |
| `inspect RUN timeline --last N` | Tool/context decisions and separate outcomes |
| `inspect RUN accounts` | Injected account text and state transitions |
| `inspect RUN decision --record latest --limit 5` | Bounded causal view from decision to actual request, evidence and account |
| `handoff export RUN NEW_BUNDLE --task-file TASK --account-file ACCOUNT --selections JSON` | Portable exact bytes with foreign provenance |
| `handoff validate BUNDLE` | Offline schema, UTF-8, hashes and attribution checks |
| `handoff render BUNDLE` | Static compatibility presentation, not releasable evidence |
| `run REPO --task TASK --run-dir NEW_RUN --handoff-bundle BUNDLE` | Fresh run seeded with local, releasable evidence; not native resume |
| `inspect RUN request --record latest --view wire --raw` | Exact outgoing HTTP bytes |
| `inspect RUN request --record latest --view compiled --readable` | Full formatted compiled JSON |
| `inspect RUN request --out NEW_DIR` | Request, context manifest, account and inspection metadata |
| `status RUN [--json]` | Last recorded activity and agent/host outcomes; liveness unknown |
| `runs list --root DIR` | Discover manifests and legacy journals under an explicit root |
| `audit RUN --strict [--output FILE]` | Independent mechanical evidence audit |
| `check --list` | Reviewed offline commands, without importing checks |
| `check --profile offline --output NEW_DIR [NAMES...]` | Default or explicit offline subset |

All inspection paths are read-only unless an explicit export/output destination is
provided. Errors identify missing paths/records instead of silently succeeding.
An incomplete final journal line is excluded from a live inspection prefix and
reported; it is never silently repaired. Raw log payloads may be binary. The
structured views use escaped JSON so malformed Unicode cannot break their output.

`run --provider course|openrouter|openai` selects an adapter. Defaults can also be
set with `MADS_PROVIDER`. `--model` / `MADS_MODEL` selects a route-compatible model;
`--reasoning` / `MADS_REASONING` selects reasoning effort for the Responses adapter.
Other adapters reject this option rather than inventing a wire setting. Adapter
configuration is not proof that an arbitrary model is compatible with the tools.
Credentials come from the existing route's environment variable and are never
printed. Resolved nonsecret settings go to stderr. `--dry-run` checks local paths,
limits, and credential presence, but does not test authorization at the provider.

## Transport settings

`--transport-timeout SECONDS` sets transport inactivity, defaulting to 120 seconds;
0 disables it. `--transport-retries N` permits N additional classified transient
attempts, defaulting to 2. Any nonnegative count is accepted; `unlimited` explicitly
removes the retry count limit. Provider retry delays are honored.

Recognized connection/timeouts, HTTP 408/500/502/503/504, and temporary rate-limit
or queue failures can retry. Quota, billing, authentication and exhausted-budget
errors stop the run. Attempts are recorded, and retries do not replay tools.
A failed connection may already have reached the provider and incurred usage.
These controls are separate from `--steps`, process deadlines, and context fit.
`--steps 0` remains unlimited.

## Layout and provenance

A new direct run contains:

```text
run-manifest.json       version, identity, nonsecret route, relative artifact refs
source/                 runtime sources sampled at startup
task.json               authoritative task string
memory/working.md       single unverified working account (agent or attributed seed)
journal/events.jsonl    append-only events
journal/blobs/          exact immutable captures
artifacts/jobs/         process capture files
result.json             distinct agent/harness/cleanup/export outcomes
```

The source snapshot records sampled file bytes. It does not certify code loaded
earlier into a custom long-lived Python interpreter. Assignment runtime staging is
separate and frozen before container launch. Source digests identify contents;
they are not semantic verification.

Readers accept the run directory, its journal directory, a legacy study directory
containing `run/`, or an assignment stage containing `output/run/`. New manifests
use relative artifact references. Historical raw records keep their original
container paths. Moving an export does not rewrite its evidence.

Assignment stages default to `.runs/<run-id>/`; `--run-root` / `MADS_RUN_ROOT` can
select another staging root. Standalone latest-attempt receipts live in
`.runs/latest-attempts/`. A receipt is invalidated before credential/setup checks,
so a failed new attempt cannot reuse an old export. Concurrent attempts for the
same task should use distinct `MADS_BATCH_DIR` directories; a standalone latest
pointer is not a multiwriter arbitration service.

`run_all.sh` allocates a fresh `batch-*` directory. `MADS_BATCH_DIR` names its
per-task receipts; `MADS_OUTPUT_ROOT` scopes patches and grading outputs. The
batch manifest identifies the task input and artifact locations. Receipts include
the selected run, export status, and patch hash. Root patch copies remain only
legacy conveniences. A current partial-work export is usable without a completed
agent claim or passing tests. Missing cleanup evidence still blocks attestation.

Inspection reads saved evidence; it does not resume a session or establish
whether its processes are still running.

## Completion ownership

Every run uses one solver followed by one fresh final review/repair owner.
`--review-prompt FILE`
changes the reviewer assignment only when explicitly selected by an operator.
`--user-message-file FILE` is repeatable and preserves exact later instructions
separately from the original task. See [solve/review](solve-review.md) for semantics.

`--steps N` limits implementation-session requests only; `0` is unlimited. The
fresh reviewer has no request limit. It is not a whole-run token or cost budget.
