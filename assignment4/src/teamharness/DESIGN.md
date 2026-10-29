# One continuing main investigation

The Python harness gives a model broad means to investigate and repair a
repository while keeping execution and evidence dependable. The model chooses
its questions, edits, shell commands, specialists, checks and conclusion. The
runtime records what actually happened and preserves the source and evidence to
which each observation applies. A plan, summary or review remains its author's
interpretation; it cannot establish a runtime fact or approve a repair.

This document owns the current operational model. [CONTEXT.md](CONTEXT.md) owns
model-facing information and continuation. The [runtime guide](README.md) owns
local commands and tool use; the [course adapter](../../lab/adapters/teamharness/README.md)
owns packaging, qualification and external execution.

## Assignment and initial investigation

`Investigation` starts one main with an explicit assignment, workspace, frozen
starting source and the capabilities its configured tools actually implement.
Literal human requirements remain distinct from delegated assignments, authored
guidance and later working summaries. A task may begin in a repository
subdirectory: commands retain that directory while source capture covers the
Git root. Initial dirty source is preserved separately from later solver edits.

New public runs use 15 exploration responses followed by one planning response.
During exploration the model can read, search, edit, use the shell and delegate.
Independent reads or searches can run concurrently within one response. Plan
publication and completion controls are absent until the decision boundary.
Text-only responses and rejected tool calls spend exploration responses;
dedicated compaction and capacity-recovery requests do not renew that allowance.

The planning request receives the last observations and exposes only a minimal
`repair_plan` continue/plan or skip/reason decision. Allocation also requests
readiness and effort estimates. The plan should explain required behavior, the
inspected entry/caller/state/consumer path, diagnosis and uncertainty, next work
and useful verification. Python validates the decision's arguments, not its
reasoning. An invalid or missing decision records `planning_timeout`, separately
from an explicit skip. No correction response is invented after this single
response boundary.

Continuing preserves the main's conversation, source, jobs and original deadline,
and restores its configured implementation tools. The model can register
commands and select guidance after continuation; it need not invent executable
checks during the decision. This is an explicit workflow boundary, not an OS
sandbox or a restriction on how the subsequent investigation must proceed.
Recorded older workflows retain their own policy and request projection.

## Actions, concurrency and processes

Each conversation has one executor lease, which protects ordered history and
effect publication. Separate conversations may deliberately share files. The
unrestricted local shell is a trusted capability, including access beyond an
assigned source copy. A private source copy does not isolate external caches,
symlinks, services or machine resources.

New sessions execute consecutive ordinary tool calls from one response in
parallel. The runtime persists group membership, original call IDs and effect
identities before dispatch, then records result messages in call order. That
message order does not establish execution order. The model must put dependent
operations in later responses; overlapping writes can race. A single multi-file
`apply_patch` supports coordinated changes. Explicit `parallel` groups use the
same executor and provide a persistent inspection/cancellation handle.

Session and group controls separate dispatch segments. Configuration changes and
compaction activate after a complete exchange; finishing is a sole-call action.
A shell result can return a tracked job while its process continues. Response
waits default to ten seconds and are not cancellation deadlines. An optional
model-chosen `kill_after_ms` is enforced by the persistent process supervisor;
when absent, the harness invents no cancellation deadline.

Arguments are checked against the exact schema advertised in that request before
dispatch. The pinned JSON Schema validator does not coerce values, insert defaults
or discard unknown fields. Operational preconditions and effects have separate
receipts. Syntax and match conflicts can reject an edit before writes; a later
write failure can leave partial changes. Intended, applied and uncertain source
must remain distinguishable. Built-in search handles paths and literal text;
regular expressions use the observable shell/job capability.

## Source, plans and evidence

A preserved snapshot retains starting source, including initial dirty files.
Candidate capture identifies exact base, patch bytes, size and hash. Grading and
registered commands run against separate exact replays rather than whichever
live files happen to exist later. Helpers use private or explicitly shared source
according to their assignment; their results do not silently replace main source.

The main can evaluate current captured source or a selected checkpoint when a
grader is attached. Receipts identify the supplier, candidate and available
counts. Eligible identical candidates can reuse original grades; a later edit or
favorable report does not extend that grade to new source. Count changes describe
outcomes, not their cause. Captured-byte equality, inequality and no comparison
are different facts; an observed source-capable action is not a comparison of
current live bytes.

`repair.py` preserves the model's prose plan and validates its executable portion
separately. A plan revision changes identity. Checks retain argv, directory,
expectation, result format, candidate and original output. Case-identity reports
can support baseline comparisons; counts alone cannot establish equivalent
failures. Finished exact-plan/exact-candidate receipts can be reused, while
partial or interrupted executions retain uncertainty. Selected guidance passages
remain attributed advice with frozen references and hashes. Delivery does not
prove that the model understood or followed them.

The immutable SQLite archive retains original requests, responses, effect intents,
receipts and prior contexts. New stores share immutable content behind the
existing objects/blobs SQL interface; legacy table archives retain their schema.
Sharing storage does not merge observation identities or change authored meaning.
Readable views and their paging are specified in [CONTEXT.md](CONTEXT.md#reading-results-and-recovering-detail).

## Delegation and returns

The main chooses concrete assignments for specialists and can continue their
conversations, inspect evidence and integrate selected patches. `spawn_agent`
defaults to current source. Starting-source or checkpoint attempts require private
source and leave the parent's files intact. These clean attempts inherit literal
human requirements and the explicit assignment without the parent's working
summary. Checkpoint review uses the same private-agent and callback machinery;
reviewer edits are not automatically integrated, and review agreement is not a
completion condition.

A return identifies its actual author and source. A busy parent receives queued
evidence under its current executor; an eligible inactive parent can awaken in
the same conversation. Archived return identities support missed-callback
reconciliation without redelivering the same evidence. After ownership transfer,
the actual parent retains the original return and the current main receives its
reference through the completed handoff chain. A late return cannot restore a
retired owner's action powers or clear an explicit human stop.

Returning or stopping a role does not kill its shell jobs. The local CLI joins
owned continuations and reports the latest settled return. Course finalization
owns a separate process-cleanup and submission boundary.

## Completion as a verified boundary

New public CLI and course entry points enable completion verification. Embedded
clients opt in; recorded workflows without that policy retain their original
behavior. A text-only main reply is an ordinary turn. `finish_role` explicitly
returns the role and requests verification of immutable captured bytes, including
an earlier selected checkpoint when supplied.

The gate distinguishes candidate identity, solver diff, exact replay, configured
commands, registered checks and the attached grader. Initial dirty files do not
count as a solver diff. An empty model-defined check list records a verification
gap without inventing a failed execution. A nonempty diff, model confidence or
exit zero alone does not establish correctness. Without a grader, gate success
cannot claim hidden requirements passed.

A passing gate accepts those exact bytes. Failed required commands or incomplete
verification return observations to the same owner. Candidate identity failures
retain the source for attention. A confirmed failing completion grade instead
publishes a durable handoff intent for a fresh main over that exact candidate.
Under the current policy, the same candidate/base and original grade trigger
that transfer once; repeating completion against already transferred evidence
returns feedback to the current owner. A new failing candidate can transfer
again. Failed interactive grades alone leave the investigating main active.

The fresh owner receives literal requirements, a dated factual brief, original
completion evidence and archive references. Its assignment makes clear that the
inherited patch is already applied and explains changed paths, HEAD's relation
to starting source and the observed remaining time. Previous executable check
definitions retain their author. Assistant history, working summaries and prior
completion prose do not become the new owner's instructions.

This review-focused owner diagnoses and repairs directly, registering its plan
without repeating initial continue/skip screening or reporting back to the old
owner. The original source remains available and the original deadline survives.
Source preparation verifies base, hash and size in staging before publication.
Interrupted preparation can reuse an exact published copy or rebuild from
immutable inputs; it never replaces an already assigned owner's working source.

Completion and grading effects have persisted intents. Reopening observes pending
or uncertain work rather than invoking it again. Durable-return selection cannot
turn an unchecked return into accepted completion. External-deadline fallback
retains its separate unreviewed status. The adapter's original-base replay,
transfer, submission and evidence collection are specified in the
[course guide](../../lab/adapters/teamharness/README.md#candidate-and-process-ownership).

## Interruption, reopening and context changes

An interrupted action may have executed. Recovery reads matching saved intent
and result receipts before declaring an outcome unknown; it does not dispatch the
effect again. Parallel recovery preserves completed members, skips unstarted
members and reports entered members without results as uncertain. Corrupt or
mismatched evidence produces a specific observation. Custom executors without
read-only receipt recovery retain uncertainty.

Explicit human continuation can resume the same responsibility. Ordinary resume
uses recorded configuration, source identity, limits and spent responses.
Missing source needs explicit restoration; changed baseline copies are preserved
rather than deleted. Explicit stop suppresses automatic wake without implying
that jobs or remote operations were cancelled.

Configuration changes and compaction are observable boundaries. Within an epoch,
material runtime changes append dated observations; clock-only changes remain in
saved state. Compaction preserves original evidence while changing the working
view. The model-written continuation, literal human inputs and runtime facts
remain separately authored. Read-only reconstruction uses saved observations and
configuration, not today's clocks or files. See [the context contract](CONTEXT.md)
for capabilities, retained exchanges, provider compatibility and interrupted
compaction recovery.

## Allocation, packaging and qualification

Allocation is explicitly enabled by the course package. Existing investigators
pause after planning, and a deterministic selector ranks their model estimates.
`allocation.py` owns ranking, `main.py` owns admission of the same session, and
the adapter owns discovery, task pools and discard/submission effects. Admission
does not renew source, deadline or response allowance. Helper reports cannot
admit a paused main. Completion takeovers inherit allocation without another
selection round. Operational ordering and failure behavior are in the
[runtime allocation guide](README.md#optional-allocation-across-course-tasks).

The builder packages this Python main workflow, required shared modules and
selected adapter code. Historical non-main workflows require their frozen
implementation; they are not silently reinterpreted. Research evidence and live
machine state are not solver input. Different architectures belong on separate
branches/worktrees; settings comparisons retain exact source, configuration,
package and cohort identities. Active campaigns retain their frozen inputs and
controller ownership.

Local scripted-provider checks establish mechanics through requests, files,
processes and exact replay. Linux environment qualification and the original
course launch check establish their stated deployment scope. Model quality and
leaderboard parity require separate evidence; neither a readable view nor a
successful local fixture supplies it. The [development guide](../../test/teamharness/README.md)
owns focused verification practice.

## Implementation boundaries

| Component | Responsibility |
| --- | --- |
| `main.py` | Investigation ownership, planning/admission, completion, takeover and helper-return reconciliation. |
| `agents.py` | Agent identity, executor leases, workspace selection, delivery and callback lifetimes. |
| `session.py`, `context.py`, `provider.py`, `presentation.py` | Complete exchanges, request construction, configuration, context evolution and readable projections. |
| `tools.py`, `parallel.py`, `patching.py`, `repair.py` | Validated actions, concurrent member receipts, exact edits and scoped executable plans. |
| `candidates.py`, `workspaces.py`, `baseline.py` | Starting source, candidate capture, exact replay and verified restoration. |
| `prompts.py`, `guidance.py`, `guides/` | Authored instructions, frozen reference material and observable guidance delivery. |
| Shared `src/api.py`, `src/store.py`, `src/workspace.py` | HTTP, immutable SQLite evidence, and process/file effects. |
| `lab/adapters/teamharness/` | Package construction, course integration, qualification, AWS preparation and reporting. |
