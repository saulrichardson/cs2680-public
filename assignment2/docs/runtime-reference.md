# Shared runtime reference

Current mechanics; see [CLI guide](cli.md) for launch and observation commands.

## Tools and evidence

`read`, `edit`, `write`, `bash`, `poll`, `send`, and `cancel` operate on real files and
processes. Calls dispatch in order; yielded processes may overlap subsequent calls.
Invalid individual calls are rejected while valid siblings proceed. Native argument
strings are never repaired. Operation status, effects, subprocess exit, capture state,
and the agent's task assessment are distinct.

Tool arguments must be a standard JSON object (no NaN/Infinity). A rejected call with
invalid JSON-object arguments remains exact in the journal. Because the tested Qwen
route rejects such arguments even in historical native calls, replay presents that
call and its effect-free rejection as explicitly labeled historical data. Valid sibling
calls retain native pairing; returned content/reasoning remains unchanged. An unfinished
response envelope executes no calls. Neither recovery path repairs or repeats an action.

File edits match literal UTF-8 against current contents; no newline normalization or
mandatory version locking. Writes use atomic replacement on the local filesystem,
resolve symlink targets, and preserve existing modes. Concurrent external writers can
still race an edit. Captured hashes identify observed bytes, not an atomic repository.

Small selected results appear completely when they fit. `output_chars` controls only
presentation: 0 returns metadata, null requests the entire selected view subject to
request capacity. Defaults are 16000 characters for processes and 12000 for reads.
Large process output shows one quarter head and three quarters tail; range reads page
forward. The split is configurable. Oversized selected views have direct paths, exact
shown/omitted byte ranges, and a continuation. Capture is not truncated by display limits.

The default model-facing receipt is a compact projection of the full recorded result.
It leads with action/status/effects, useful output and a short scope statement: full artifact,
prefix, suffix or interior selection, with display completeness distinct from capture status.
A completely displayed 500-byte prefix of a 19,329-byte artifact explicitly remains a prefix.
Exact `text_parts`, artifact size, selected/shown byte intervals, source/time and capture or
decoding errors remain visible. `details` gives `history_read` arguments for the full receipt;
the journal and captured artifacts retain their detailed descriptors and original bytes.
Ordinary observations, historical retrieval and pinned evidence use this same presentation.

`read` defaults to 250 lines. Its `range` is `"full"`, `{"lines":[1,20]}` (inclusive,
one-based), or `{"bytes":[0,100]}` (half-open). Pass range objects as JSON objects, not
strings. `output.next_read` is one direct read action: first recover omissions within the
selection, then advance beyond its end. A fully displayed suffix offers no forward action
back to an earlier prefix. Explicit ranges can still retrieve those bytes. This is local
observation coverage, not a ledger of what the model has seen or understood.

UTF-8 decoding failures remain explicit; `encoding="base64"` retrieves exact arbitrary bytes.
Content contains no inserted line labels. Coordinates index the immutable artifact;
`source.job_byte_range` gives its process-log origin without repeating coordinate aliases.
An immutable path (once, either directly or in `next_read`) identifies the saved bytes.
Reading a growing log or editable file takes a new snapshot. Source metadata distinguishes
these new observations from historical retrieval; earlier test output does not establish
correctness after later edits. An exact capture of an agent-authored diagnostic establishes
what it produced, not that its conclusions were independently verified.

Processes use pipes, not PTYs; stdout/stderr are merged. A managed reader drains continuously
with bounded buffering, including during generation. Polls capture new intervals; hidden
parts are read through artifacts, not repeated polls. Accepted stdin bytes must not be
resent. EOF follows queued input; undelivered input is preserved on pipe failure.
Shell exit and live group membership are separate. Cleanup tracks owned groups, not
intentionally detached processes; the assignment container supplies stronger containment.
Storage failures stop affected jobs and block further model effects. Capture is complete
only after observed writers end, EOF is seen, and storage succeeds.
Both `send` and process state expose cumulative `stdin_accepted_bytes`,
`stdin_written_bytes` and `stdin_pending_bytes`, with accepted = written + pending.
Successful pipe writes do not prove application consumption. `stdin_eof_requested` records
an explicit close request; `stdin_eof_sent` records closure of the write end after that queue
drained, not the child's acknowledgment of EOF. `stdin_queue_open` describes harness
acceptance, not receiver readiness. Per-send `accepted_bytes` remains distinct from the
cumulative total. Errors and exact pending input remain available, including writer-startup
failure and cleanup with queued bytes. State and pending-byte snapshots are taken under
the input lock. Historical process states retain their sample time; they are not fresh polls.

## Working context

Ordinary tools edit the single working file. Before each request its exact snapshot is
included once at the request tail as agent-authored, unverified `working_account`, beside fresh
observations and runtime facts. The original user requirements remain authoritative at the front.
The `current` label means the latest loaded snapshot, not validated contents. Other
memory files persist but are not automatically read. Missing memory is explicitly empty;
unreadable, invalid, or oversized memory uses an explicitly stale last fitting snapshot
(or an absent account) so the model can repair the current file. If the previous snapshot
also cannot fit the retained history, neither account is injected. The repair notice
identifies the omitted snapshots and working-file path; the files and evidence survive.

`context_restart` selects exact historical records/excerpts and preceding exchanges. It
uses the account after all sibling calls finish. Its own complete exchange remains.
Nonempty UTF-8 observations include a copyable `output.retain` entry for `keep_records`. It
selects the whole selected interval, including undisplayed bytes; narrow it to what the next
action needs. Empty or out-of-bounds exact byte selections reject instead of silently becoming
empty excerpts. Ordinary reads remain EOF-tolerant.
The compiler checks the whole candidate before applying or rejecting selection; unrelated
effects are never rolled back. Selected excerpts protect their exact bytes independently
of duplicate historical output bodies, which may still be masked or paged. Updating memory
alone does not remove old interpretations. Restart's `applied_context` includes `account_read`
for the actual injected immutable snapshot and `retained_evidence` normalized to direct
`history_read` arguments with byte ranges. One `replay_omission` marker names the latest
removed exchange span and a bounded history-search action. It describes omission from
replay, not irrelevance or assumed understanding; older history remains searchable. A new
replacement updates this marker instead of accumulating a marker per observation.

`context_status` reports committed settings at call time. `context_configure` stages changes
until the completed response batch; multiple configuration calls merge in dispatch order.
Configuration and restart are validated together against the final candidate and effective
input capacity `min(input_limit, total_limit - output_tokens)`. A rejected proposal keeps
the previous policy and evidence selection, includes a usable rejection in the next request,
and preserves sibling file/job effects. Controls followed by completion are not applied.

Context inspection describes what is automatically included, restart selection rules,
retrieval-only history and supporting files, and unchanged filesystem effects and running
jobs. `injected_account` gives the exact account record, state and immutable full-read
arguments from the last finalized request, plus a direct read of that compiled request and
`manifest_read` arguments for the complete manifest through `history_read`.
It does not resnapshot the editable working file: same-batch edits and pending controls
are explicitly outside its scope. The estimate and calibration ratio both describe that
finalized request, even if later usage has updated calibration for future requests.
References and distinctions between observations and
inferences are useful account guidance, not a required format or an adjudication service.

The shared system guidance teaches a concrete workflow: investigate, extract what the
remaining task needs, correct the account, retain essential exact evidence, release completed
exploration, then continue the next action. It encourages leading the account with the
deliverable, current action, completed work and unresolved questions, followed by supporting
findings; no fixed schema or schedule is imposed. Update notes when a meaningful observation,
completed subtask or changed plan makes them inaccurate, not merely near a context limit. Before
restart, reconcile them with latest evidence and remaining requirements. Concrete examples cover
source exploration to the next edit, implementation progress versus unverified work, long-document
findings and quotations, contradictory observations, and a disconfirming test. After replacement,
continue the retained next action; retrieve a missing detail rather than routinely rereading the
codebase. Changed files or uncertainty can justify broader rereading.

These examples use existing whole-exchange replacement and exact evidence selection.
Body-only masking is a resource fallback, not semantic memory management: it can hide
observations while retaining the interpretations they should correct. There is no new
selective-release tool, second handoff account, automatic summarizer or truth checker.
Fresh global facts carry `kind="fresh_global_state"` in `harness_runtime` (or the existing
`CURRENT HARNESS FACTS` wrapper), separate from historical samples. The tail contains the
one latest working account and compact counts, capacity/headroom and omission information.
Complete policies, selections and provenance remain in `context_status`, manifests and restart
acknowledgments. Historical account-writing calls are past actions, not the current plan; global
facts embedded in a tool result were not produced by that tool. Native tool pairing, item order
and provider continuation data remain unchanged. Expected usability benefits and total request
compression are not claimed as demonstrated results.

Default estimated
thresholds are masking at 160000 and pressure notice at 180000 tokens, with 10 recent
exchanges protected. The newest exchange is always protected from policy masking, even with
`keep_recent=0`; hard-cap paging still applies. Disabling masking with `mask_at_tokens=0`
restores surviving masked outputs from immutable captures to their prior bounded displays.
Increasing `keep_recent` restores that protected suffix. Removed exchanges stay removed and
commands are not rerun. Normal input is capped at 220000 with 32768
reserved generation tokens against a configured 262144 total window. Estimates use
serialized bytes with conservative calibration, not an exact endpoint tokenizer.
Endpoint acceptance and reported limits remain authoritative.

Each candidate snapshots the account and samples runtime state once. The request facts,
applied-control acknowledgements and manifest derive their selection from that candidate.
`previous_context_id` explicitly names its predecessor; the request record links to its
own manifest. Each manifest retains the finalized request blob, serialized byte length,
final estimate, effective policies, account identity, selected records and masked groups.
In-prompt estimates are explicitly scoped before telemetry to avoid a circular count;
the capacity decision includes all final telemetry and notices. Pressure notices also
consider headroom for replaying the next generated response.

When delivery does not fit, the largest unprotected output body is paged down first,
including previously delivered bodies, with ties resolved by exchange and call order.
This preserves small outputs where possible. Recent protection applies to threshold masking,
not an impossible promise that old payloads can consume all room for a subsequent response. No semantic
importance scoring. Explicit exact evidence, original requirements and native assistant
calls/reasoning are not truncated. Eligible older payloads may be masked. Genuine
irreducible overflow records `capacity_blocked`; no automatic whole-exchange deletion.
An actual context rejection permits one logged smaller corrective attempt with internally valid
policy thresholds. Classified transient transport failures permit bounded recorded retries;
quota/billing/authentication errors remain terminal. See [transport settings](cli.md#transport-settings).
Executed tools are never automatically repeated.

`history_read` distinguishes record envelopes from attached payloads. `history_search`
uses literal matching, optional type/range filters and a stable journal head. API bodies
remain historical data. Request/response bodies and supplied reasoning are saved; this
proves client-side representation, not upstream template rendering. The journal is
append-only under harness operations, not tamper-proof against unrestricted shell access.
History receipts label compiled requests, outgoing HTTP bodies, returned responses and
native assistant messages distinctly, with related record IDs and direct body paths.
`python -m mads.logs JOURNAL --request REQUEST_ID --full` follows that request's manifest,
transport, assistant calls, outcomes and referenced evidence, stopping at the next request.

Course is primary. OpenRouter Qwen uses the same tools and compiler. For an explicitly
controlled experiment, `OPENROUTER_PROVIDER` pins a provider slug with fallbacks disabled;
omitting it uses OpenRouter's routing. Actual upstream provider identities are in responses.
Pinned route fields are included before capacity validation. The adapter records the logical
request and actual HTTP body separately, and checks their JSON equality before transmission.
The published Qwen template was checked independently: wrapped synthetic notices preserve
the latest user boundary; a plain later user message changes it. This is template evidence,
not proof that hosted Course/OpenRouter servers use that exact template or preserve thinking.

The direct OpenAI route uses `gpt-6.1-sol` through Responses with medium reasoning,
standard mode, `all_turns`, `store:false`, and the default service tier. Function schemas
explicitly use `strict:false` to preserve the ordinary optional tool arguments. The compiler
projects the complete Responses body before estimating capacity. Returned native output
items, IDs, phases and encrypted reasoning stay exact in retained exchanges; a separate
execution view dispatches function calls using their `call_id`. Encrypted reasoning is opaque
and is never decoded or summarized. Serialized encrypted bytes count toward the conservative
capacity estimate, so this limit is not an equal amount of usable model context across routes.
Incomplete or malformed envelopes produce no effects. Their exact historical rejection is
available in subsequent context; invalid argument calls are projected individually with their
paired effect-free rejections. Ordinary Responses text, including a `final_answer` phase, is
nonterminal under the same explicit `finish` contract. The SDK may change JSON key ordering;
the separately saved logical and HTTP bodies are checked for JSON equality, not equal hashes.

Ordinary scripts can aggregate saved artifacts, compare structured files, or run several
programs and print a selected report. Keep their source and full data in ordinary files;
the result is a model-authored transformation. There is no additional code-composition API,
persistent interpreter, automatic resume, delegated-agent runtime, PTY, or native visual input.

## Current default ownership

The shared Agent defaults to one solver then one fresh final reviewer/repairer.
The summary-only finish is a proposal in the solver and terminal in the reviewer.
See [solve/review](solve-review.md) for the lifecycle and failure handling. The
[embedded prompt inventory](../mads/prompts/README.md) identifies actual SYSTEM and
USER assembly. Original task, explicit later user messages and active session
assignment survive context restart; historical evidence remains fallible data.

## Persistence and process limits

The journal has one writer. Interrupted appends preserve complete records;
an uncertain partial tail stops further appends for operator recovery. Final
outcome files use atomic replacement, which does not guarantee power-loss
durability. Failed storage can prevent final recording or export.

Process groups cannot contain children that detach into another session.
Incomplete output capture or failed cleanup blocks an attested export. Use the
assignment container as the outer process boundary; SIGKILL or machine loss can
prevent cleanup entirely.
