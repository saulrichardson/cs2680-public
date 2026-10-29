# The model's information and continuing context

This document owns how assignments, observations, interpretations and archived
exchanges reach the Python harness's model. [DESIGN.md](DESIGN.md) owns execution
and investigation lifecycle; the [runtime guide](README.md) owns operating
commands. The review standard below describes what changes should preserve.
Specific implementation descriptions must still be checked against their producer
and actual serialized request; a design requirement is not proof of delivery.

## Authorship and the working understanding

Literal human requirements, delegated assignments, authored guidance, runtime
observations and model interpretations have different sources. They retain that
distinction through delivery, compaction and delegation. A model's explanation
of a failure does not become a grader diagnosis. A source action does not prove
that live files match an earlier captured candidate.

`append_user` is exclusively human input. `append_message(text, sender=...,
relationship='parent'|'child'|'peer', identity=..., resume=True)` records attributed
model messages. `append_observation(value, identity=..., resume=True,
automatic=True)` records runtime notifications. Original text and authorship stay
in the control journal. Automatic notifications and model messages cannot clear
an explicit human stop; literal human continuation can resume the conversation.

`set_task_brief(text, author=..., identity=...)` records an attributed working
interpretation separately from the task. The model can update that same brief
through `configure_session(reason=..., working_brief=...)`. Its actual session
author and source response are recorded, and delivery identifies the new version
as superseding the earlier interpretation. Earlier versions remain archived.
Useful content is the current question, supporting and contrary observations,
unresolved corrections and the next useful action. There is no per-turn reporting
requirement. The brief does not replace human requirements or registered checks.

`task_context()` returns original human-input references and the latest working
brief, including queued human followups. A parent can supply these separately
from its delegated assignment. Starting-source and checkpoint attempts retain
literal human requirements without inheriting the parent's working summary.
Compaction retains literal inputs by default; `retain_inputs=False` deliberately
omits them from the working view without deleting their originals.

## Append-only working context

New sessions use `context_policy: append_only_v1`. Within an epoch, guidance and
working-brief updates become new attributed messages rather than rewriting the
beginning of the conversation. Material runtime changes append dated observations
with original archive references. Initial runtime facts arrive once per epoch;
subsequent updates contain changed sections. Clock ticks and job elapsed-time
changes alone do not add another inventory.

The provider request has one leading system message. Later harness observations
accompany the latest tool result with separate attribution, or use an attributed
user message when there is no tool result. Historical late-system observations
project into supported roles without rewriting their archived originals. This
preserves ordinary tool-loop continuity on the pinned Qwen template.

Job observations identify the process, observed state, actual exit when known,
output path and durable result reference. State changes during inference appear
at a later request boundary. Unknown supervisor outcomes stay unknown; a dated
observation cannot promise the process is still running now. Active jobs and
uncertain effects remain available after compaction, while old delivered exits
remain retrievable without repeating their whole inventory.

Plan views carry plan/check identities, applicability and original execution
references rather than repeatedly injecting the full prose plan. Candidate
comparisons distinguish same, different and not compared. Environment metadata
retains its supplied scope; a check-definition hash does not identify all
executable bytes, dependencies, caches or services. These distinctions are properties of the actual observation and its scope.

Existing sessions without a recorded context policy retain `legacy`. An explicit
`reconfigure(context_policy=...)` or `configure_session` transition can select the
future projection without changing identity or replaying effects. Ordinary resume
does not silently upgrade recorded menus or policies.

## Reading results and recovering detail

Readable views derive from original receipts and retain stable object references.
Each view answers its call first: one process-status sentence with command and
output, numbered file text, `path:line: text` matches, changed files with a diff,
a grade sentence, an attributed report or one view per parallel-group member.
Identifiers and storage paths follow under Details. Each result ends with its
evidence references: object, original JSON and full readable view. Format changes
must preserve or update consumers of those markers, including the receipt test
fixture, fleet reporting and guidance audits. Original receipts retain exact field
values even when the view abbreviates them.

Shared rendering in [presentation.py](presentation.py) keeps the producer's field
order and hierarchy and shows clocks in UTC. Fields recorded as null appear on one
`not recorded:` line for their block; an empty collection appears as `(none)`. A
view that knows a null means not applicable omits it, such as a command with no
cancellation timer. Purpose-specific views own which details appear first.

File reads identify path, source hash and displayed line/byte range. Each line
carries a number-and-tab prefix that the view and tool description label as
display-only, so numbers are not copied into edits. Search views list
repository-relative matches with scan coverage and continuation; per-file source
hashes follow. A truncated search is not evidence that no other matches exist.
Edit views distinguish intended changes, applied changes and uncertain effects,
with per-file outcomes and original before/after evidence. A patch view lists
changed files and shows a diff of applied files built from the archived bytes.

Messages the harness injects are labelled text rather than serialized JSON. The
course adapter gives the public task as literal PROBLEM STATEMENT, REQUIREMENTS
and INTERFACE sections, followed by a separate RUNTIME section with the UTC
deadline and grading scope. Session facts in the system prompt are labelled lines.
Delegated agents receive literal human inputs with their archive references.
Harness notifications lead with the event; a child return names the agent, status
and outcome, then the attributed report, patch, workspace and assignment. Agent
messages and configuration or compaction results are attributed text. Each
original remains in its control record or receipt.

Command observations retain command, directory, process state and output address.
A response wait is not a process deadline. Long output first observed from byte
zero can show literal beginning/end ranges with an explicit omitted middle.
This is an abbreviated observation, not a contiguous page or a claim that all
middle bytes were read. Its cuts prefer a nearby line boundary while reporting
exact offsets. The default cursor then continues after that preview's
observed end; explicit offsets retrieve omitted or previously displayed ranges
as contiguous pages without advancing the default cursor. Later default polls
continue from their saved position. A preview-read failure retains the original
command observation and reports the display failure separately.

Offsets are zero-based bytes, while file line selections are one-based. Text
paging finishes UTF-8 codepoints where supported, potentially including up to
three bytes beyond the nominal page limit. Continuation must use the actual
returned offset, not an assumed character or line count. Archive pages state their
byte range and next offset before the content, so continuation survives a clipped
view. Original output remains addressable even when an abbreviated view omits ranges.

Automatic agent returns and explicit inspection are separate delivery paths.
Both must keep the actual author and source visible and identify the report as
interpretation. Runtime status and source/artifact references must not become
part of the model's purported report. Queued and takeover-routed returns retain
the original return identity; formatting cannot grant a retired owner new powers.

`search_history` searches readable renderings of original records across agents
and epochs: messages and model responses with decoded tool calls, receipts and
controls. Excerpts are discovery aids, not new observations. Exact `read_archive`
arguments identify the source object and message. A complete archived tool
exchange can also offer `read_exchange`, using `include_exchange=true` and
`json_pointer=/body/messages/N` on a session-state object. It returns all calls,
with decoded arguments, and their matching results as labelled text, with the
epoch and original object hash. The same reference without `include_exchange`
returns exact JSON. Incomplete exchanges produce a specific error rather than
fabricated results. Large selections retain byte paging, and reading historical
actions never executes them.

New SQLite stores share immutable JSON pieces and binary payloads behind the
existing objects/blobs views. Read-only SQL reconstructs identical full bodies
and hashes. Legacy tables retain their representation. The storage representation does not change the memory or recovery protocol.

## Configuration and provider compatibility

`Session.reconfigure(reason=..., ...)` explicitly changes future instructions,
tools, settings, compaction instructions, attributed guidance or provider. The
host must stop or wait for the executor; transitions require a complete tool
exchange. They do not start inference, replay effects, move the workspace,
replace session identity or renew external limits.

`Session.recorded_config(root)` reads the journal's configuration. Reopen with
that configuration; ordinary construction rejects accidental drift. Each
transition archives its version, reason, author, previous reference and complete
configuration. Later requests identify their configuration and provider. Earlier
requests and receipts remain unchanged.

The model's `configure_session` accepts an explicit reason and supported changes
to `system`, `guidance`, `tools`, `settings`, `tool_execution`, `context_policy`,
`compaction_prompt`, `model` or `working_brief`. Activation follows the complete
exchange, with a separate application result. A stop before activation records
non-application without automatic replay. Model-authored versions identify
`model:<session_id>` and the originating response. Guidance is attributed
`author`/`text` material. A working brief is interpretation, not an instruction
that gains authority merely by entering configuration.

ToolRuntime validates descriptors against capabilities it implements. A schema
cannot invent an executor effect. Model changes use the current endpoint and
protocol; credentials, destinations, workspace identity and external limits
remain host-owned. For an explicit host HTTP transition, construct
`HTTPProvider(root, configuration={'protocol': 'chat_completions',
'endpoint': ..., 'model': ...})` and pass it as `provider=`. The initial
`provider-config.json` remains historical; the session journal owns later versions.

Ordinary reasoning text, refusal metadata and native message extensions remain
archived and can survive model changes. Retained encrypted continuation is not
portable across model/routes and produces a specific incompatibility error.
Explicit compaction with the current provider can first produce an attributed
textual continuation. HTTP preservation alone does not establish what a provider's chat template includes in model input.

## Compaction and complete exchanges

Compaction changes the working view while preserving the archive. The same model
writes a continuation in a dedicated no-tools request. Its instructions preserve
requirements, evidence references, rejected explanations, unapplied corrections,
uncertainty, unfinished work and active job/agent identities. This remains a
model interpretation rather than a new runtime verdict.

A successor epoch retains the original session assignment, literal human inputs
unless deliberately omitted, the latest working brief, the continuation and
separately recorded runtime facts. New sessions retain one recent complete tool
exchange as quoted evidence. `retain_recent_exchanges` selects another count,
including zero, through the compact tool, host method or queued request.
Incomplete exchanges are not assembled into completed ones. Quotes are labelled
text with decoded calls; they identify original records and do not install
provider-bound native continuation or redelegate historical actions. Tools,
source and process lifetimes survive.

A provider capacity rejection can use same-model no-tools recovery, followed by
complete bounded archive traversal when needed. It does not silently prune
history, invent a token budget or disable source edits. An explicit
`max_request_bytes` bounds serialized bytes per recovery request, not estimated
tokens. Each traversal request presents exact substrings of the JSON-serialized
messages under a header naming the source object, message and byte range, so
coverage remains reproducible without a second JSON wrapper. Full source coverage
is required before a new epoch activates.

## Interrupted context recovery

An explicit HTTP `finish_reason: error` on a no-tools compaction generation can
retry that request through existing transport backoff. Each attempt and its known
or unknown usage remains archived. A settled failed generation gets a new relay
request identity; a lost transport reply retains the same identity. Upstream
uncertainty or a no-retry signal prohibits regeneration. Original deadlines and
explicit stop apply before attempts and during backoff. This recovery never
replays shell, source or grading effects.

Bounded traversal saves successful model drafts, exact source/configuration
identities, byte budget, covered ranges and next position. Reopening and explicitly
compacting the same source can reuse those drafts. Changed source or configuration
starts a different traversal. Partial drafts do not become replacement context.
Malformed summaries and deterministic rejection leave the original conversation
intact. Responses arriving after the external deadline remain archived without
executing further actions.

## Initial planning request scope

Planning policy version two projects tools from the saved phase without changing
the configured inventory. Exploration omits publication/completion. The single
decision response forces the minimal continue/skip tool. Execution checks this
request's advertised capabilities, so a provider-returned editing call cannot
execute during the decision. Continuation restores the configured tools.
[DESIGN.md](DESIGN.md#assignment-and-initial-investigation) owns the lifecycle;
reconstruction uses the recorded phase, narrowed schema and forced tool choice.

## Reconstruction and review evidence

Request construction saves runtime observations before inference. Read-only
reconstruction uses that saved state and configuration rather than sampling
current clocks, files or jobs. `prefix_structure` compares leading messages,
menus and model within an epoch. It measures logical structure; actual cache hits
require provider usage evidence.

Useful focused witnesses include `test_context_evolution.py` for authored
configuration and serialized requests, `test_stoppage_recovery.py` for interrupted
recovery, and `test_information_views.py` for information delivery. Inspect the
specific fixture and current implementation before choosing a named check. These
are local mechanical witnesses, not proof of summary quality, repair performance
or Linux/course qualification. Follow the
[development guide](../../test/teamharness/README.md) rather than running a broad
suite for routine assurance.

## Review contract for model-facing changes

A purpose-specific projection owns semantic ordering, grouping, labels and which
content appears first. Shared rendering preserves those choices. It must not
silently alphabetize fields, flatten relationships or assign the same meaning to
absence, unknown, not applicable and an empty collection. Producers own those
meanings. Consequential empty values must remain visible, and additional original
details must remain retrievable.

Review the full path from producer through projection, rendering and paging into
the complete request, including actual roles and available tools. Follow direct
results, automatic notifications, queued returns, reopening and compaction where
relevant. A fixed inspector does not establish a fixed return notification. A
typed wrapper around already mixed prose does not separate its authors.

Check literal fidelity, source/range identity, attribution, complete exchanges,
deliberate order and reachable omitted detail. Cursor movement alone does not
prove delivery: compare displayed ranges and the declared continuation policy.
An abbreviated view must expose its gaps rather than imply all prior bytes were
read. Generated variation can explore Unicode, paging boundaries and missing
fields when authorized, but needs the right contract and successful public-path
witnesses. The ordinary limits on broad testing still apply.

Keep consequential rationale beside its contract or implementation, linked to
available evidence. Update current prose when behavior changes instead
of accumulating contradictory addenda. This adds no mandatory solver report,
fixed decision schema, new memory service or prescribed investigation sequence.
Presentation fidelity and repair-quality improvement remain separate claims.
