# Shared context lifecycle

Course is the primary deployment target. Course, OpenRouter and OpenAI use the same `Agent`
loop, `SYSTEM` guidance, tools and `Context` compiler. Provider adapters handle endpoint
options and Chat Completions versus Responses representation; they do not select a different
memory policy or prompt.

The next request keeps the authoritative task at the front and places one automatically
loaded working account beside fresh observations at the end:

```text
system guidance + actual run paths
original user task and caller-supplied constraints/corrections
selected exact historical evidence
retained native exchanges, with paired results and continuation state
  final result.harness_runtime:
    fresh process facts + compact context telemetry + working_account
```

Without a tool-result tail, the existing wrapped `CURRENT HARNESS FACTS` message carries
the same state. For Chat the carrier remains `role=tool`; for Responses it remains a
`function_call_output` with its original `call_id`. Native items keep their order, identity,
reasoning and opaque continuation fields. The account is no longer a separate message
before native history. Global state is supplied by the harness, not produced by the tool
whose result carries it. Fresh facts are replaced on each compilation and are not committed
into internal historical tool results for later replay as fresh state.

| Component | Authority and meaning | Lifetime and retrieval |
| --- | --- | --- |
| Original task | User requirements, distinct from agent interpretation | Preserved at the front; caller-supplied corrections remain part of the task |
| `working_account` | Agent-authored and unverified findings, progress, hypotheses and next actions | One end-of-batch snapshot of the working file, refreshed before each request |
| Account state | `current` identifies the latest loaded bytes, not verified or semantically up-to-date contents | Missing/stale/absent fallback remains explicit; `context_status.injected_account.read` retrieves exact injected bytes |
| Historical account writes | Past actions and arguments, not the current plan | Preserved in native exchanges until whole-exchange replacement; journal remains retrievable |
| Tool observations | Exact captured output with source, time, version and scope limits | Full artifacts persist; inline output may be paged or masked with direct retrieval |
| Selected evidence | Agent-selected exact bytes, not a validated interpretation | Automatically replayed until replaced or cleared through `keep_records` |
| Runtime facts | Fresh process/compiler observations, not carrier-tool output | Sampled for the next request, explicitly labeled `fresh_global_state` |
| Compact telemetry | Exchange/masked/selection counts, effective capacity, generation allowance, omission marker and scoped estimates | Fresh tail projection; complete policies and selections remain in status, manifests and control acknowledgments |
| Omission marker | Latest replacement's excluded historical exchange span, without a relevance or understanding judgment | Bounded historical search action; earlier omitted history remains searchable |
| Completion | Agent assessment of actual work against the original task | Only `finish` requests completion; claim, cleanup and export remain separate |

`context_status` expands routine compact facts into the committed policy, selection and
account details. Its `manifest_read` gives `history_read` arguments for the complete finalized
manifest; `compiled_request_read` links the exact request. Inspection does not resnapshot
pending working-file edits or apply sibling controls. Restart acknowledgments continue to
provide the actual `account_read`, normalized retained-evidence actions and omission marker.
There is still one working account. Optional `context_restart(account="...full reconciled text...")`
writes that file before staging context replacement; `expected_account_sha256` can guard the
observed version. Invalid arguments/selection bounds and duplicate restart reject before writing.
A later capacity rejection does **not** roll back the account write or other ordinary effects.
The receipt reports `effects` and `account_update` hashes; acknowledgment `account_read` is the
actual end-of-batch sample, which a later sibling write may supersede. This is not an atomic
file/context transaction or a semantic truth check. Retry rejected selection without repeating
already successful effects. Ordinary `write`/`edit` remains available.

The model should update its account when a meaningful observation, completed subtask or
changed plan makes it inaccurate. It should not wait for capacity pressure or update after
every tool call. Before restarting, it should reconcile the account with recent evidence and
remaining user requirements. A useful account leads with the deliverable, next action,
completed work and unresolved questions, without a compulsory schema.

The production prompt includes concrete examples using the existing tools:

- After source investigation, record relevant interfaces, intended edits, remaining obligations
  and requirement/evidence references. Reconcile edits already made, unresolved failures and
  unverified work. Retain the exact interface and test scaffolding for the next edit with `keep_records`
  and restart, then perform the edit. Retrieve a specific missing interval when necessary.
- After implementation starts, replace “not implemented” with the actual changes and what
  remains unverified. Record a failed test's output separately from the suspected explanation,
  revise the next action and retain the necessary evidence before replacement.
- After reading a long document, preserve task-relevant findings, essential quotations and
  retrieval references. Release the bulky reading history once sufficient for the next action.
- Keep contradictory evidence available while resolving uncertainty, and change the approach
  when a test disproves the account's explanation.

After restart, continuing the retained next step is the default. Broad rereading can be justified
by changed files or unresolved uncertainty; it should not be an automatic reorientation ritual.
A retrieval pointer does not retain its contents. Account references alone do not inject exact excerpts. `keep_records` omitted preserves existing
selections, `[]` clears them and a supplied list replaces them. `keep_recent_exchanges` chooses
a suffix of preceding whole exchanges; the current exchange remains paired. Files, jobs and
ordinary effects survive. Failed combined controls retain the previous policy and selections.

## Exact retention and reversible masking

For nonempty UTF-8-mode output with no observed decoding error, `output.retain` supplies a copyable `keep_records` entry:

```json
{"record_id":42,"part":"payload","view":{"bytes":[0,2048]}}
```

It selects the **whole selected output interval**, including any undisplayed bytes. Narrow
its byte view to the exact interface, test scaffolding or quotation needed for the next action.
A selector is not a summary, relevance judgment or claim that the model saw every selected byte.
`observation_record` names the captured bytes; `details.record_id` names the tool receipt.
History reads preserve their selected record and part. Empty intervals, base64 displays and
observed decoding failures receive no retention affordance. Undisplayed bytes have not been
validated by a bounded preview; a selection can still fail exact UTF-8 or capacity checks.

Unlike ordinary EOF-tolerant reads, exact retention rejects empty selections and byte ranges
that exceed artifact bounds. A mistaken receipt selection gets a diagnostic pointing to its
observation when available; the compiler does not silently redirect it. `part=record` still
allows deliberate retention of a nonempty event envelope. Accepted restart acknowledgments
normalize retained ranges and identify the injected account. Rejected controls preserve prior
context and recorded sibling effects; no successful write or command is rerun or rolled back.

Body-only masking remains a resource fallback: it can hide observations while retaining the
interpretations they should correct. The newest exchange is protected from **policy masking**,
even with `keep_recent=0`. This gives fresh observations a first presentation opportunity, not
a promise of unlimited space. Hard-cap paging and irreducible capacity failures still apply.

`context_configure` with `mask_at_tokens=0` restores surviving policy-masked outputs to their
previous bounded displays. Increasing `keep_recent` restores newly protected surviving exchanges.
Restoration reads immutable captures, not current files or rerun commands. It preserves the
original selection and bounded display, remains subject to hard-cap paging, and never brings
back exchanges removed by restart. Rejected combined controls leave the prior policy in place.
Capacity-paged outputs remain bounded; use their continuation for additional bytes.

Whole-exchange replacement remains the supported way to release completed investigation.
No selective release, automatic summary, semantic truth checker, second account, rigid schema
or mandatory restart schedule is added. The final fit includes account, facts and telemetry.

Finalized request blobs, exchange representations and actual outgoing HTTP bodies preserve
what was included in each request; they remain available through `context_status`, the
journal and `mads.logs`.
Included bytes do not establish model comprehension.

## Requirements and test judgment

The shared prompt keeps the original problem, requirements and interface authoritative.
Notes, diagnoses and plans are fallible interpretations. A real implementation defect calls
for a code fix. An old assertion directly contradicted by a requested behavior change may
need an explained update that preserves useful coverage. For example, changing a default can
invalidate the old default assertion; an unrelated parsing failure still needs investigation.
An expected application error belongs in a passing negative test. Environment and collection
failures differ from executed failing assertions. Conflicting original statements remain
available with their references, a defensible interpretation and unresolved uncertainty.
Passing tests do not excuse a missing explicit requirement. Official score, requirement
correctness and execution validity are separate outcomes. No grader or completion gate changed.

## Verification and evidence limits

The checks `checks.evidence_delivery`, `checks.mask_restoration` and
`checks.retention_recovery` cover fresh-output protection, strict selection, immutable
restoration, hard-cap paging and rejected replacement with surviving ordinary sibling effects
in both Chat and Responses projections. They save compiled requests for inspection. Existing
context, presentation, protocol and independent journal-audit checks cover surrounding contracts.
The auditor checks emitted retain selectors against authoritative observations. These local
checks are mechanical evidence, not live provider validation.

### Explicit file-evidence version checks

`evidence_check(record_ids=[N])` compares a saved whole-file observation with
its current resolved source path. A search-page record follows its explicit
source record. Each item reports `same`, `changed`, `missing`, `unavailable`,
`unstable`, or `no_file_source`, with immutable old/fresh read actions and hashes.
The check does not change source files or automatically replace retained evidence.
Use the fresh read/retain selector deliberately after understanding the change.
A matching hash means sampled bytes match, **not** that an interpretation is true,
that a test covers a requirement, or that the repository was atomically captured.
Process intervals, diffs and reads of anonymous immutable blobs have no inferred
live-file baseline. Checks are explicit (maximum 20 records), not recurring I/O on
every model request. Historical records and their original bytes stay available.

### Retention with an explicit purpose

A `keep_records` selection may add a nonempty `purpose` string, for example
`{"record_id":42,"view":{"bytes":[0,1200]},"purpose":"Reducer interface for the next edit"}`.
It is emitted beside the unchanged exact excerpt as agent-authored, unverified data,
and is included in restart acknowledgments, context status and saved manifests.
Omitting `keep_records` preserves existing purposes with their selections; replacing
or clearing the list replaces or clears them. A purpose is not a new requirement,
a semantic relevance check, a summary substituted for bytes, or a filesystem watch.
It consumes normal request capacity. Existing unlabeled selections remain valid.
