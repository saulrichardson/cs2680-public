# Python team harness

This is the operating guide for the maintained Python main workflow. Start with
[DESIGN.md](DESIGN.md) for lifecycle and responsibility boundaries and
[CONTEXT.md](CONTEXT.md) for the model's information contract. The
[course adapter](../../lab/adapters/teamharness/README.md) covers packaging and
external runs.

Install the pinned runtime dependency before local use or building:

```sh
python3 -m pip install -r src/teamharness/requirements.txt
```

Course packages carry the validator as hashed Python source, so task images need
no dependency installation. Active tool schemas, examples and rejection feedback
use the same contract. Validation establishes argument shape, not successful
execution or repair correctness.

## Main, specialists and review

The main works directly in assigned source and chooses useful specialists. Only
the main receives `evaluate_candidate` when a grader is attached. Failed
interactive grades leave it investigating; confirmed failed completion grades
have the separate [handoff behavior](DESIGN.md#completion-as-a-verified-boundary).
There is no automatic implementer/test-author/integrator sequence.

Use `spawn_agent` with a concrete assignment and deliberately choose private or
shared source. Its default is `source="current"`; `source="starting"` and
`source="checkpoint"` require `workspace="private"`, and the latter requires
`checkpoint_id`. Starting/checkpoint attempts retain literal requirements without
the parent's working summary. Private copies can still share external caches,
symlinks and services. Inspect returned evidence and explicitly integrate selected
patches; a report does not prove correctness.

`finish_role` returns the model's report and can select a captured `checkpoint_id`
without overwriting later files. Main text-only replies remain ordinary turns;
specialists can return reports as text. Returning does not kill jobs. The local
CLI waits for owned helper/continuation quiescence and reports the latest settled
main return. Ctrl-C stops owned agent activity without implying cancellation of
existing processes or replay of uncertain effects. Course finalization owns a
separate cleanup, exact replay and submission boundary.

## Completion verification and fresh ownership

New CLI runs and packages enable a completion gate. `finish_role` requests checks
of exact captured bytes, solver diff, replay, configured commands, registered
checks and the attached grader. A passing gate accepts those bytes. Incomplete
checks and failed required commands return evidence to the same owner; candidate
identity failures need attention. A confirmed failing completion grade can
transfer its exact candidate to a fresh diagnosis-and-repair owner under the
original deadline. Repeating the same already-transferred evidence does not
create another owner. The [design](DESIGN.md#completion-as-a-verified-boundary)
explains the retained source, evidence and ownership invariants.

Add project-specific checks with repeated `--completion-check-argv` JSON arrays,
for example `--completion-check-argv '["python3", "-B", "-m", "pytest", "tests/unit"]'`.
Checks run in separate exact replay copies at the Git root and support
`{workspace}`/`{patch}` placeholders. Exit zero and unchanged candidate source are
required; full stdout/stderr and source identities are saved. A command result is
not promoted to an official grader verdict. `--no-require-diff` permits no-edit
assignments; `--no-completion-gate` retains ungated conversation completion.
Embedded clients explicitly opt in through
`Investigation(manager, root, completion_gate=True)` or a policy dictionary with
`enabled`, `require_diff` and named `checks` (`name` plus an `argv` array).
Existing recorded workflows retain their original policy on resume.

## Local use and continuation

```sh
# Configure A4_BASE_URL, A4_MODEL and A4_API_KEY for Chat Completions.
python3 -m src.teamharness run \
  --task /absolute/public-task.txt --workspace /absolute/git-repository \
  --output /absolute/new-evidence-directory

# Same main identity, workspace, model settings, original limits and jobs.
python3 -m src.teamharness resume \
  --output /absolute/existing-evidence-directory \
  --message 'Continue with this additional requirement.'

python3 -m src.teamharness status --output /absolute/existing-evidence-directory
```

A local grader is optional. Supply `--grader-argv` as a JSON array; `{workspace}`
and `{patch}` placeholders are substituted without a shell. The command runs in
a clean replay directory. To supply candidate counts and trends, its entire
stdout must be a JSON object with integer `tests_failed` and `tests_total`, where
`0 <= failed <= total`. Logs can go to stderr. The harness preserves raw output,
exit status and before/after source identities. It does not guess counts from
text or infer official success from exit zero. Source-changing graders do not
supply eligible counts. `--official-grader` explicitly identifies the configured
command as official; its authority is never inferred.

`--max-agents` and `--deadline-unix` record actual configured limits. When absent,
no capacity or task deadline is invented. Ordinary resume retains the recorded
configuration and does not renew limits or silently upgrade capabilities. Explicit
future configuration changes are supported in the same conversation. An uncertain
grade/effect is retained without automatic replay. Missing working source needs
explicit restoration; changed preserved baseline source is recorded and restored
from the verified archive into a new directory without deleting the changed copy.

Human input, delegated messages, runtime observations and working summaries
retain distinct authorship. Compaction changes the working view while preserving
original evidence, source and processes. See [CONTEXT.md](CONTEXT.md) for retained
inputs, explicit configuration and recovery behavior.

## Explicit configuration evolution

New conversations default to `temperature: 0.6`; explicit values override it.
Resume and compaction preserve recorded settings, including historical omissions.
This changes client requests, not an existing serving engine or frozen package.

Use `configure_session` for supported future instructions, guidance, tools,
settings, execution mode, context policy, model or working-brief updates. Changes
activate after the complete exchange and retain original configuration versions.
Ordinary resume uses recorded configuration rather than silently upgrading it.
The [context contract](CONTEXT.md#configuration-and-provider-compatibility)
describes host APIs, author identity, provider compatibility and failure behavior.

## Guidance from previous investigations

The frozen [reference catalog](guides/README.md) routes by mechanism and language.
The system prompt supplies concise guidance and discovery pointers; long manuals
stay available through ordinary reads. Frozen file hashes are recorded in the [bundle inventory](guides/provenance.json). These are authored techniques,
not task answers, runtime observations or measured model-quality improvements.
Configuration can select attributed guidance without replacing the original task.

## Evidence and existing tools

Use `read_archive` to retrieve original records or byte pages and `search_history`
to discover exact references across agents and context epochs. A complete exchange
link returns the original calls, with decoded arguments, and their results as
readable text without executing them; the same reference without it returns exact
JSON. `task_status`
reports observed agents, jobs, contexts, deadlines and evaluated candidates; an
earlier grade does not establish the current live source's state.

New sessions run consecutive ordinary calls in one response concurrently and
publish tool messages in call order. Choose independent operations; put tests
that depend on an edit in a later response. Concurrent writes can race. Use one
multi-file `apply_patch` for coordinated changes or an explicit `parallel` group
when you need an inspectable/cancelable group handle. Group members have separate
receipts; a failure does not roll back siblings. Cancellation requests and actual
process exits remain different observations.

The initial `tool_execution` is `parallel`; old sessions without that field retain
ordered behavior. Configuration and compaction apply after complete exchanges;
`finish_role` is a sole-call operation. Custom embedded executors must be
thread-safe or provide independent `fork()` callbacks. The maintained runtime
constructs a ToolRuntime per concurrent member. The course relay omits an
unsupported optional parallel-generation hint at its wire boundary while retaining
native multi-call execution; see the adapter guide.

Each conversation has one executor lease. Independent conversations may share
source deliberately, and the unrestricted shell can access beyond a source copy.
An interrupted action may have executed. Continuation reads original receipts or
reports uncertainty instead of repeating effects. `edit_file` rejects mismatched
replacement counts before writes; `apply_patch` validates all hunks before writing
but a later write failure may leave partial changes. Observe per-file outcomes.

## Readable observations and shared runtime facts

Tool views retain source and effect identity, requested content, original receipt
references and complete saved views. `read_file` uses one-based line selection;
archive and output offsets are zero-based bytes. Display-only source line numbers
are not patch content. Long output views identify their shown and omitted ranges;
use an explicit job offset or original output path to read any omitted section.
Patch results list changed files with a diff of the applied bytes. The task,
session facts and harness notifications reach the model as labelled text.
See [result delivery](CONTEXT.md#reading-results-and-recovering-detail) for cursor,
paging, authorship and retrieval semantics.

`exec_command` and `job` response waits default to ten seconds. A running job is
still active when the wait ends. `exec_command(kill_after_ms=600000)` supplies an
explicit ten-minute process-group cancellation timer; omission creates none.
Poll the existing job rather than launch another command to observe it.

Saved runtime observations make original requests reconstructible. Task facts
come from the session; run-wide counts, GPU and elapsed/limit facts come only from
the owning controller when supplied. Local or unavailable observations leave
that scope unavailable. Changed observations do not invent a new task budget.

## Planning, selected guidance, executable checks and adversarial review

New public runs use 15 exploration responses and one constrained planning
response. Batch independent repository reads/searches during exploration. The
last observations reach a forced `repair_plan` continue/plan or skip/reason call;
checks and guidance selections are registered after continuation. Allocation also
requires readiness/effort estimates. A missing valid decision is
`planning_timeout`, separately from a model-authored skip. `--planning-turns`
changes the exploration allowance; resume does not renew it.

Plans are repository-grounded prose: required behavior, inspected path, evidence,
diagnosis and uncertainty, next changes or a distinguishing experiment, and
useful verification. The model chooses its investigation. Python validates the
arguments, not the explanation. Continuation restores configured tools in the
same conversation; review-focused takeover owners register their plan directly
without another initial screening. See [planning scope](CONTEXT.md#initial-planning-request-scope).

### Optional allocation across course tasks

The course builder's `--select-tasks X` (fixed implementation target) or
`--skip-tasks N` (opened-task count minus N) enables a separate run-wide allocation
policy. The controller opens all available tasks and runs their existing planning
investigations with a bounded `--planning-workers` pool (default 3). Each sole
continue/skip decision includes `assessment={"readiness":"experiment",
"effort":"small"}`. Readiness is repair, experiment, search or blocked; effort
is small, medium or large. These are model estimates supported by the existing
plan/reason and observations, not feasibility or correctness verdicts.

Both decisions pause as `planning_complete` after the complete exchange. A skip
is advisory in this mode. An exhausted planning window remains explicitly
`planning_timeout` in the saved planning result, with no invented assessment.
The pause preserves the session, source, jobs, plan and spent responses. It accepts
no candidate and requests no completion takeover. Late helper observations stay
archived and cannot allocate implementation. Shell experiments and edits remain
available during exploration; this is not a read-only security boundary. The
exploration/decision allowance does not bound wall time or total helper compute.

The deterministic selector orders eligible tasks by readiness (repair,
experiment, search, blocked, then unknown), effort (small, medium, large, then
unknown), positive recommendation, public-content hash, and task ID. The public
hash only breaks ties; it supplies no task-specific knowledge. Selection is
independent of acquisition order for the same identities/content. Negative
recommendations do not reduce the target: the controller chooses up to the target
from eligible tasks.
An unavailable, failed or expired investigation is retained but not eligible.

The controller records the partition before admission. `Investigation.admit`
resumes the original owner with an attributed allocation observation, including
when its recommendation was negative. It never changes that recommendation,
renews its original deadline or resets planning usage. Every selected owner is
admitted immediately after selection; no implementation-worker queue spends its
deadline while another task runs. Shared model admission controls actual inference
concurrency. Planning across the full queue still consumes original task windows.
Completion checks and exact-candidate grading remain unchanged. A later fresh
completion owner retains the task's allocation and proceeds directly to independent
diagnosis, without a second run-wide selection round.

The implementation target is not an automatic refill target. After selection,
unselected tasks are explicitly discarded alongside selected implementations:
task-owned jobs are stopped, exploratory source is archived separately, and an
empty patch is submitted. Their sandboxes do not wait for selected work to finish. Exploratory edits never
silently become an accepted or submitted repair. All tasks still count in course
grading. Selection quality and planning overhead require live measurements.

### Registering and running checks

Planning and implementation use the same conversation. Planning does not disable
edits or shell commands; this is a workflow boundary, not a read-only OS sandbox.
`repair_plan(action="set", plan="...")` records the explanation, next
probe and verification approach. Earlier versions remain in the archive; replacing
the plan creates a new identity and invalidates prior-plan completion evidence.

Only executable inputs are structured. `checks` contains named `argv` arrays,
optional repository-root-relative `cwd`, `expectation` and `result_format`.
`guidance` contains paths relative to the frozen guide root and optional exact
Markdown headings. The tool returns those actual passages with file hashes and
attribution. The full library remains readable through existing tools. The main's
current plan and selected passage references survive context compaction as runtime
references to model-authored material, not authoritative explanations.

Example tool arguments (paths depend on the frozen catalog):

```json
{
  "action": "set",
  "plan": "Reproduce paste through the controlled parent, trace its value into the next render, then repair the responsible handler.",
  "guidance": [{"path": "contracts.md"}],
  "checks": [{"name": "component tests", "argv": ["npm", "test", "--", "CodeInput.test.tsx"], "cwd": "frontend"}]
}
```

`repair_plan(action="run", target="baseline")` executes checks in separate copies
of preserved starting source. `target="candidate"` double-captures current source,
or accepts an existing `checkpoint_id`. Each check runs on a separate exact replay;
its logs, candidate hash, process status and source-integrity result are retained.
The original task deadline bounds command process groups. These commands use the
same trusted execution capability as existing grading; private copies do not
isolate caches or services. A test introduced by the repair may be absent from the
baseline; that absence does not establish a behavioral reproduction.

Default `result_format="exit"`, `expectation="pass"` requires exit zero and intact
source, without claiming test discovery. For reliable test-identity comparisons,
a check may emit its entire stdout as `{"cases":{"test-id":"pass","other":"fail"}}`
with `pass`, `fail` or `skip` values and diagnostics on stderr. With
`result_format="cases_json"`, `expectation="no_new_failures"` requires a valid
baseline for this plan and check. New failing identities, missing previously
executed identities and new skips fail comparison. Counts or exit codes alone
cannot establish equivalent failures. Task requirements should use `pass`;
model-selected checks never replace configured commands or the attached grader.

When useful, the main can request `checkpoint(review=true)`. The existing agent
manager starts an adversarial reviewer in a private copy verified against that
checkpoint. Literal human requirements survive; the main's working summary does
not. The reviewer uses real probes and returns prose findings with evidence.
Use existing inspect/wait tools to receive the report and resolve supported
findings. Repeated review requests for identical bytes reuse the recorded agent.
A report about an earlier checkpoint does not cover later edits. Experimental
reviewer edits are not automatically integrated. Review is optional.

Completion consumes registered checks under the frozen gate policy. Prose-only
plans record a gap; reviewer delivery and approval are not required. Finished
exact-plan/exact-candidate receipts may be reused; interrupted work remains
uncertain. See [the completion lifecycle](DESIGN.md#completion-as-a-verified-boundary)
for same-owner feedback, fresh ownership and fallback distinctions.

## Verification

Use the [focused verification guide](../../test/teamharness/README.md) to select
checks for an implementation change. Local checks exercise execution mechanics;
provider acceptance, external grading, and repair quality need their own evidence.

## Focused context with stable history

Within a context epoch, material changes append dated observations and working
interpretations; clock-only refreshes do not repeat inventories. The model can
update its working brief or explicitly compact. `retain_recent_exchanges` selects
how many complete exchanges survive as quoted evidence, including zero; new
sessions default to one. Original records remain retrievable. Details and review
standards are owned by [CONTEXT.md](CONTEXT.md), rather than duplicated here.
