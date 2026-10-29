# Persistent-main course adapter

This adapter packages [the Python team runtime](../../../src/teamharness/README.md)
for the existing course task sandbox and original external grader. It builds and runs the main investigation. The
[runtime design](../../../src/teamharness/DESIGN.md) owns investigation and
completion behavior; [CONTEXT.md](../../../src/teamharness/CONTEXT.md) owns the
model-facing information contract. This guide owns external operating behavior.

## Build and execution

```sh
python3 lab/adapters/teamharness/build.py --output /absolute/new-package
```

The builder freezes the runtime, specialist prompts, guides, shared HTTP/SQLite
and source/process effects, and course adapter. The campaign explicitly identifies
`workflow: main`. Counts-only feedback is enabled by default; `--no-grade-feedback`
omits that capability. `--max-agents` declares an actual configured capacity;
there is no default quota. `--solve-seconds` declares the course solving window,
which defaults to 3,600 seconds across the main, children and grading waits.
Task installation precedes that window. Capture, transfer and final original
course grading follow it; outer fleet lifetimes are separately configured.

The worker gives the model the public task as literal PROBLEM STATEMENT,
REQUIREMENTS and INTERFACE sections, then a separate RUNTIME section with the UTC
deadline and grading scope. It saves the original fields and runtime facts as
`run/public-task.json`; `observe.py` fingerprints that record, falling back to the
JSON task string that earlier packages recorded in `workflow.json`.

### Task allocation

To allocate implementation across the entire course queue, opt in when building:

```sh
python3 lab/adapters/teamharness/build.py --output /absolute/new-package \
  --skip-tasks 13 --planning-workers 3 \
  --model-slots 3 --engine-profile stock-c5
```

This opens all tasks, runs planning with the existing investigators, records a
deterministic partition and targets `max(0, opened tasks - 13)` implementations.
For 26 tasks that is 13; for five tasks it is zero. The original readiness,
effort, recommendation and public-fingerprint ordering is unchanged. Failed or
expired investigations cannot be admitted, so an eligibility shortage can leave
the implementation target unfilled. `--select-tasks N` remains available for a
fixed implementation count; the two modes are mutually exclusive.
The configured planning policy uses 15 exploration responses followed by one
constrained decision response. This boundary is owned by the runtime planning
path; the allocator consumes its structured result without a second model judge.
The same sessions continue, including selected negative recommendations. Waiting
consumes the original solving window; admission never creates a fresh deadline.
The planning pool bounds initial investigations. All selected owners resume together;
the shared model queue controls inference concurrency across them and their helpers.
There is no separate implementation-worker queue or --solve-workers setting.
The course still evaluates every task, including explicit discards. Without
either allocation option, the existing one-task-per-worker lifecycle remains unchanged.
The [runtime guide](../../../src/teamharness/README.md#optional-allocation-across-course-tasks)
specifies ordering, unknowns and the retained model/runtime distinction.

`madsServe_logs/allocation/` records policy, opened tasks, pre-dispatch selection,
results and any shutdown/controller failure. Each task retains its planning
result, original requests and phase command intents. A lost dispatch reply is
never retried. The allocation directory and finalization intents refuse automatic
re-entry; recovery must inspect existing receipts rather than request tasks or
repeat operations. Unselected exploratory edits are separately archived and an
empty patch is submitted after task-owned process cleanup.
The partition records intended selection; actual admission command receipts and
task grade receipts establish what ran. Tasks can still expire during the whole-queue planning phase. Selection rechecks
original deadlines, and admission never renews them. Selected owners do not wait
for another implementation to finish. Failures and uncertain operations remain
separate outcomes.
Failures do not trigger automatic backfilling from unselected tasks.

### Shared model admission

`--model-slots` separately enables one shared queue for owner, helper and
compaction requests across all task relays. It admits requests in FIFO order
within each task and round-robin order across tasks. A stable request identity
lets a retry retrieve its original completed response after a lost local reply.
Caller disconnects do not release upstream capacity. Interrupted upstream work
retains unknown usage and occupied capacity; remaining known capacity may still
serve other owners. If all slots are terminally uncertain and no local upstream
worker remains, queued requests receive upstream_capacity_unknown without being
dispatched, and new requests are rejected with the same reason. Original unknown
operations and their occupied slots remain intact; no regeneration or cancellation
is inferred. Task retirement cancels queued work and records actual active
workers without delaying capture or final grading. These workers remain tracked
by the shared queue; retirement does not claim upstream cancellation. Each task's
`model-drain.json` and `madsServe_logs/model-scheduler/` retain that state. The
enclosing server lifecycle owns cancellation after the agent exits. The queue
adds no generation timeout or model parameter change. Without this option the
relay keeps its existing direct path.

### Engine assets

`--engine-profile stock-c5` includes the exact three engine assets from the
previous stock C5 submission. Its name identifies provenance, not a task-count
setting. It keeps the checkpoint, 262144-token context, 0.90 memory fraction,
seed and backends; it enables neither MTP nor an engine request cap. The course or
matched lab supplies the pinned SGLang environment and `a4-serve`. No AHE solver,
dependency bundle or model weight is copied. Without a profile the builder keeps
its existing runtime-only output. See [engine provenance](engines/README.md).

The example above is an integration configuration, not a measured optimum for
the Python harness. Planning concurrency, model slots and engine settings are
independent choices. Local checks establish mechanics; a matched Linux/GPU run is still needed
before attributing performance or repair quality to this combination.

For a built allocation package, run `check_allocation.py --package ... --output
/absolute/new-check`. It uses independent actual sandbox servers, worker
subprocesses and a scripted HTTP model. It covers a negative recommendation
selected for further work, the same session/deadline, completion takeover,
discarded source edits and reconstruction of all serialized requests. It does
not measure model quality or establish course GPU launch readiness.

### Guidance publication and analysis

New course builds include the maintained portable repair library by default. The
builder verifies publication hashes and packages only the solver tree, excluding
human evidence and historical outcomes. The main starts with catalog routing and
loads selected passages during planning or later investigation. The long portable
SYSTEM_PROMPT.md remains available as a reference rather than being injected into
every request. `--repair-guidance research/repair-guidance/solver` selects an explicit
publication; `--no-repair-guidance` creates a control without that library.
Existing campaigns and resumed teams retain their frozen files and role prompts.
Run `check_main.py --require-repair-guidance` with the built package to exercise
actual reads, subsequent requests, edits, checks, candidate replay and grading reuse.
The maintained workflow's executable planning and adversarial checkpoint review
are described in [the runtime README](../../../src/teamharness/README.md).

For read-only post-run analysis, `audit_guidance.py` streams immutable archives
and checks their full compressed SHA256/length while selecting native records.
Pass `--input` with private rows containing `arm`, `source_key`, `instance_id`,
`url`, `sha256`, and `bytes`; URLs should identify the exact version in the durable
storage pointer. Pass the solver publication directory as `--documents`, a new
`--output`, and optionally `--workers`. Keep signed URLs and machine bindings
ignored. The reader executes no historical solver/grader code and emits no input
URLs. It checks actual following requests before counting a full skill body as
delivered. It produces per-attempt derived transcripts and a completion report;
retrieval and shell-write classifications do not establish comprehension or
repair correctness. Run it near the S3 evidence rather than restoring a fleet
locally. Preserve concise findings and remove temporary source copies afterward.

### Worker execution and completion policy

New packages enable the runtime's completion gate. Repeated
`--completion-check-argv` JSON arrays add project checks; `--no-require-diff` and
`--no-completion-gate` explicitly adjust the packaged policy. Existing workflows
retain their frozen policy on resume. The
[runtime completion guide](../../../src/teamharness/README.md#completion-verification-and-fresh-ownership)
explains same-owner feedback, failed-grade takeover and original deadlines.
Final/deadline fallback remains separately unreviewed. The adapter's exact source
selection, transfer and submission sequence is specified below.

The worker uses the stock portable glibc or musl Python in the real task container.
The main edits its assigned source. Private helpers copy source and installed
ignored dependencies, which can consume disk and initialization time. Source
hashes exclude ignored dependencies. Local initialized gitlinks retain private
Git metadata; opaque or absent dependencies retain that state. Unrepresentable
nested edits produce a specific capture error instead of disappearing.

The agent container belongs to task and inference networks. Its relay forwards
Chat Completions requests to the configured course route using an ephemeral
local bearer token. Actual provider credentials stay in the agent container.
The relay adds no model selection, reasoning conversion or repair policy. It omits
the unsupported optional `parallel_tool_calls` generation hint at the course wire
boundary, recording original/forwarded hashes in the task archive. Native multiple
tool calls and concurrent execution remain enabled; message/tool/result contents
are preserved.
The stock course preflight requires the OpenAI SDK. Before local runtime use or
building, install `src/teamharness/requirements.txt`. The builder bundles pinned
`fastjsonschema` Python source and its license into the hashed runtime archive;
task-side portable Python needs no pip installation or platform-specific wheel.

## Candidate and process ownership

A normal `main_candidate` preserves the main's selected captured bytes, including
an earlier checkpoint. If the controller disappears after a durable main return,
`main_returned_candidate` retains those returned bytes despite later live edits.
The adapter replays the selected candidate from the preserved starting source
in a separate directory and captures its patch against the original course base. It submits the resulting exact bytes; finalization
records the initial, selected and original-base identities separately.

Finalization uses `capture` to finish that replay and publish a small manifest
for the patch, candidate, selection and finalization receipts. The adapter first
transfers those four files, verifies their hashes and course-base relationship,
and submits the exact patch through the existing grading bridge. It records the
submission intent before the call and a receipt only after a confirmed return;
an already graded identical patch retains its original grade.

The separate `export` operation then collects the complete evidence archive.
The archive's candidate files must match the earlier transfer before extraction.
Only after collection does `done()` finalize original grading and release the
sandbox. This order keeps a large trace archive from delaying patch submission
while preserving evidence before the course controller destroys its sandbox.
A failed candidate transfer or uncertain submission still attempts collection,
but cannot authorize submission replay or task closure. Export/collection failure
leaves the exact available receipts and an incomplete task, never a claimed pass.
The legacy combined `finalize` operation remains for existing explicit recovery
tools; frozen packages retain their own behavior.

Without a durable return, finalization can capture remaining nonempty main source
as an unreviewed fallback. It never substitutes a specialist's source or selects
by an inferred correctness score. The solver worker must have exited before finalization. A selected immutable
candidate is verified and replayed in a separate directory, independently of
process cleanup. Cleanup results, including uncertainty, are recorded separately.
Without a selected candidate, live fallback capture must establish stable bytes. Local role completion
and explicit stop keep shell-job lifetimes independent; course finalization owns
the external task cleanup boundary.

An explicit worker `resume` retains the original deadline, capacity, main identity
and source. It writes a separate attempt receipt and never replays uncertain
commands or grading effects. Collected archives omit live source directories but
retain the frozen starting-source archive, including its private Git data and
offline dependencies. That archive alone does not establish that the other live
processes and environment required for continuation still exist.
Historical non-main campaigns must use their frozen runtime.

Evidence lives under `madsServe_logs/<task>/teamharness/`: original nested journals
and provider records under `run/`, candidate and selection identities, finalization,
stop receipts and original grading outcomes. SQLite sidecars are retained. Usage
accounting recovers disposable copies rather than modifying original journals.
Live workspace/dependency directories and relay-token configuration are excluded
from exports. The frozen baseline archive is retained in full. Its transport uses
four bounded parallel 512 KiB range reads through the existing course interface,
checks each range and the final size/SHA-256, and records transfer timestamps.
These reads do not repeat commands or grading. This avoids serial transport
overhead for large offline dependency snapshots without dropping evidence.

## Local check and AWS tools

```sh
python3 lab/adapters/teamharness/check_main.py \
  --package /absolute/package --repo /absolute/checkout --output /absolute/new-evidence
```

This exercises a scripted HTTP provider, real sandbox/relay/worker processes,
compaction, specialists, repair from 3/10 failing to 0/10, checkpoints,
dirty-source replay, final grade reuse and selection verification. The receipt
identifies its platform; macOS evidence does not establish Linux or GPU readiness.
See [AWS preflight](aws_preflight/README.md) for its current implementation boundary.

`environments.py` freezes reference-free task preparation and checks the real
images offline before starting model lanes. Its default `--dependency-mode stock`
uses each task's original `docker_image`, binding that immutable image to both
solver and verifier: the provided course runner selects `sandbox_image` for both.
The original manifest, controller, host runner and test scripts remain intact;
explicit dependency profiles below instrument new frozen runner copies.
The generated asset-preparation script removes reference-solution requirements
and the reference-informed Go warmer. No failure-selected package list, latest
upstream lockfile or extra CUE installation is supplied.

An explicit `--dependency-mode base-dependencies` is a development intervention:
it fills a new Go cache from only the starting repository's dependency graph.
It never applies a reference patch or invents new module requirements. It is not
claimed equivalent to the official submission environment, and the mode is
retained in qualification receipts. Candidate dependency changes are handled by
the provided verifier's test script and normal package managers through the
explicit dependency profile below. Official deployment capabilities still require
separate validation.

Startup checks exercise the packaged runtime and exact source capture in the
supplied task image. They do not recognize repository names or require a
project-wide build, dependency resolution or test collection before investigation.
The model observes project-command results through its ordinary tools. Startup
checks do not establish a correct repair or leaderboard parity.
For a new AWS development campaign, the `environments.py` CLI defaults to
`--solver-dependencies network --grading-dependencies network`. Solver sandboxes
retain their private agent/tool network and gain an additional bridge gateway;
verifier containers use bridge networking. Both use the real checksummed Go
module proxy and automatic toolchain selection, and ordinary package managers
can reach their repositories. Solver network access requires matching grader
access. Pass both options as `offline` to retain the historical control; the
programmatic `freeze` defaults remain offline so existing callers do not silently
change treatments. No guessed package list, stub module or reference patch is
installed. Candidate bytes, replay/test mounts and verifier commands are preserved.

Qualification records solver and grader access separately. The solver probe uses
the actual private-plus-bridge topology before model admission. Registry probes
establish connectivity, not the completeness of an arbitrary future candidate's
graph; use the model's real package manager and build/test commands against that
candidate. Invalid versions, source defects and unavailable registries remain
observable command failures. This AWS profile is not official offline environment
parity. Frozen active campaigns keep their original inputs.

`launch.py` rejects the known reference-dependent preparation path for new runs.
Existing frozen campaigns are historical and must not be patched or relabeled.

`launch.py` prepares and admits one existing fleet controller. Profiles select the
full pinned HARD-51 manifest or explicit sample tasks. The admitted controller exclusively owns
dispatch, collection and termination. Preparation does not launch resources;
explicit launch publishes a one-shot intent and starts the frozen controller.
An existing or uncertain launch receipt prevents automatic redispatch. Launch requires an explicit main package and a
reviewed launch plan with its actual lifetime and budget envelope.

`report_fleet.py` reads native teamharness archives and binds outcomes to selected,
submitted and graded bytes. A generic historical fleet summary may expect another
harness's receipt layout; missing parsed grades are not evidence that grading did
not occur. Confirm outcomes from original course results and exact patch identity,
retaining missing or mismatched evidence as unknown. `observe.py` reads active sandbox facts, actual
requests and runtime/guide identities through the frozen transport. Neither a
scheduled lane nor a process launch establishes useful model concurrency.

New `environments.py` freezes include the correction to the provided controller's empty-final
submission boundary: after a continued task, an empty final patch receives its own
original-evaluator result rather than retaining the prior interactive grade and
patch. The controller integration keeps that outcome tied to its submitted candidate.

`finalize_run.py` is an optional one-shot postprocessor. It observes the exact
controller/resources, reports outcomes and closes only the matching budget
commitment after verified cleanup. It cannot provision, terminate, restart or
change tasks. Recovery tools and frozen campaigns retain their original evidence
and ownership contracts; they do not select an alternative scheduler in new builds.

## Course task continuation and observed limits

For a self-hosted GPU rehearsal, `prepare_gpu.py` takes explicit original course
and public manifest paths, using the maintained `lab/replica` by default.
`--spec` freezes named variants over one cohort with a shared registry;
`--image-snapshot` binds an exact retained serving image. It freezes the current
Python package, cohort and tooling without provisioning. Its default is
12 public tasks, select6, 15 exploration responses, three planning workers, three
shared model slots and the existing 3,600-second task allowance. The original
course task path can instead use `--count 6 --no-allocation --workers 6`, or
`select_tasks: null` and `workers: 6` in the frozen settings. This starts six
ordinary task owners sharing the configured model slots; each retains its own
planning decision and deadline. It adds no cohort-wide ranking stage. The original
course controller retains unlimited live tasks, three graders and its four-hour
run limit. The existing empty-final-submission correction is recorded separately;
original evaluators remain unchanged. No historical solver or replay corpus is
included. Launch the result through its own frozen `tools/launch_iteration.py`;
that existing supervisor and collector own dispatch, evidence and termination.
The task handoff archives the completed preflight's outputs before invoking the
original controller, whose previous-output check otherwise rejects Python's
preflight scheduler logs. CPU image preparation overlaps GPU bootstrap; preflight
waits for accepted server dispatch and readiness. Compatible successor cells use
the existing server ownership/reset checks and fresh source/session state. Preflight outputs remain in the collected
evidence.
The reduced cohort establishes neither full-cohort throughput nor private
leaderboard parity. Preparation alone is not Linux/GPU qualification.

Ordinary workers repeatedly acquire and finish tasks until the dispatcher is
exhausted or shuts down. Allocation retains its separate acquisition and selection
path. Runtime facts report this agent process's elapsed time and active task count;
they do not invent a platform run cap or GPU inventory. The configured per-task
deadline remains visible and enforced by the existing worker.

An unselected acquired task still submits an empty patch and closes with
`allocation_discarded`. The retained course dispatcher exposes submission and
`done`, with no distinct skip/discard API; exploratory edits are never submitted
for these tasks.

After selection, unselected tasks collect evidence and close alongside selected
implementations. Their sandboxes do not wait for the selected owners to finish.
Preparation and planning failures retain `planning-error.txt`, appear in the
allocation result's failures and emit an stderr pointer to the original traceback.
Their final reasons are `preparation_failed` or `planning_failed`; an expired
investigation records `campaign_cutoff`, rather than a deliberate allocation skip.
If implementation was requested but no usable plans remain after failures, tasks
are closed once and the adapter reports an error after recording the result.
It does not retry uncertain actions, invent feasibility judgments, reset deadlines
or backfill failed selected owners. These diagnostics do not establish the cause
of an earlier leaderboard failure without its original receipts.

The [replica guide](../../replica/README.md) describes frozen batch launch and
collection. `report_gpu.py` reads those collected batches with whole-cohort and
selected-task denominators, exact original-grade checks, queue/service/token
measurements and explicit unknowns. The [Harvard adapter](../../harvard/README.md)
uses the same frozen inputs for original launch checks and native screens;
its external fixture checks are never promoted to original benchmark grades.
