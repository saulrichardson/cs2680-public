# Inspect one recorded decision

```sh
python -m mads inspect RUN decision --record 123
python -m mads inspect RUN decision --record latest --limit 5
python -m mads inspect RUN decision --record 123 --item 7 --preview-chars 1000
python -m mads inspect RUN decision --record 123 --offset 5 --limit 5
```

This is an offline, read-only JSON projection using the existing `Journal`. It
needs no provider credentials, SDK, or network. `RUN` accepts the existing run,
journal-directory, manifest-backed directory, and legacy-layout resolution. The direct entry point
`python mads/inspection/inspect_run.py RUN decision ...` also works.

It does **not** replay a run, reconstruct an allegedly exact wire request, infer
comprehension, or certify that a recorded tool outcome is still true.

## Selection and causality

`--record` accepts a journal ID of these event kinds:

| Selected event | Resolution |
| --- | --- |
| `invocation` | Its `assistant_record`, then that assistant's response and HTTP request. |
| `tool_result` | Its `invocation_id`, then the same chain. Also exposes `selected_outcome`. |
| `assistant` | Its `response_record`, then the response's `http_request_record`. |
| `response` | Its exact `http_request_record`; a uniquely linked assistant if recorded. |
| `http_request` | That capture; a uniquely explicitly linked response/assistant if recorded. |
| `request` | The logical compiled request and context, **without choosing a response or HTTP attempt**. Select an ID from `request_attempts` to disambiguate. |

The default `latest` means the newest complete **request, http_request, response,
or assistant** record in the observed journal prefix. A newer pending request
wins over an older completed decision. Later invocation/result records do not
change the anchor; their explicitly linked observations are still included.
An empty journal, or one containing only a partial line, returns `unavailable`.

Backward links must be explicitly recorded, target the expected event type, and
precede the referring record. Forward selection requires exactly one child
explicitly naming its parent. Missing, future, wrong-type, ambiguous, and
conflicting links are reported, not filled in by proximity or a global call-ID
search. A conflict among `request_record` links suppresses the ambiguous compiled
request/context but preserves an explicit response-to-HTTP link. Merely having a
nearby request is not evidence that it caused the selected response.

`request_attempts` separately inventories explicitly related `api_attempt`,
`http_request`, `response`, `api_error`, and `api_retry` records. This inventory
can include earlier failures and later/pending siblings of the same logical
request. It never replaces the selected causal response. A logical `request`
selection intentionally has partial state until an attempt/response is selected.

## JSON map (schema version 1)

- `selection`, `chain`, `causal_links`: selected record, compiled request, actual
  captured HTTP request, returned response, native assistant, and causal context
  manifest. Links are typed/chronology checked; recorded link fields remain
  available on source references.
- `request`: `view`, exact source reference, bounded settings, item inventory,
  optional `selected_item`, original task reference/positions, and participant
  notices. `--view wire` is the default. `--view compiled` explicitly inspects the
  compiled payload instead; it does not claim those bytes were sent.
- `response`: native items, separate argument previews, HTTP status, assistant
  finish metadata, response metadata, and **selected-response-only** `usage`.
  Chat uses the saved assistant message (or the first returned choice if no
  usable assistant payload exists). Responses uses its native `output` items,
  including reasoning, messages, and calls, not a Chat-shaped reconstruction.
  Encrypted reasoning remains opaque. Pointers refer to `native_source`.
- `invocations`: arguments and outcomes for that assistant only. Chat call IDs
  are matched within the selected assistant; Responses additionally matches
  `native_item_id`. Outcomes are joined by `invocation_id`, never by call ID
  across turns. `focused_invocation` lets an invocation/result anchor navigate
  its own outcomes. Unexecuted/rejected native calls are not fabricated into
  invocations. `response_rejections` retains the recorded rejection.
- Outcome `evidence`: explicit `observation_record` / `output_record` references,
  including account-update observations; `output_artifact` exposes a recorded
  output hash/path when present. References include recorded source provenance
  when the evidence has it. Other receipt detail remains in the source record.
- `context`: causal manifest source, injected account snapshot hash/path (not
  today's working file), sampled controls, retained-evidence selections and
  sources, groups/masking, and replay omission. Account authorship comes from
  the selected snapshot: it may be agent-authored or an operator-supplied seed.
  The account's source record is linked. Accounts and retention purposes remain
  fallible contributions, not independently verified facts.
- `source_identity`: hashes/paths from existing manifest/task identity discovery,
  sampled at inspection time, separate from response usage. This is not a claim
  about loaded source or causal inclusion. Aggregate usage remains in `summary`.
- `journal`, `issues`, `state`, `bounds`: observed prefix identity and diagnostics.
  `available` means no issue was discovered in this projection, **not** complete
  execution, application success, or a full audit of every omitted blob.
  `partial` retains usable evidence alongside explicit missing/ambiguous data.
  `unavailable` means there was no selectable latest anchor.

No captured causal HTTP request is reported as `no_captured_causal_http_request`,
with a null wire link. If the HTTP event exists but its body is missing, corrupt,
non-JSON, or not an object, the source identity is retained where possible and
its payload/request state reports the problem. Compiled bytes are never silently
substituted. An application error or failed tool outcome can itself be fully
inspectable; inspection state is not the outcome's success status.

### Original requirements and participant notices

The original task reference is the unique earlier `run_start.task`. Positions
are zero-based entries in the selected request's `messages` or `input` whose
`role` is `user` and whose string `content` exactly equals that task. Missing,
ambiguous, or transformed/block-encoded task content is explicitly not matched;
there is no semantic guess or fallback to the current task file.

`participant_notices` recognizes only top-level user strings beginning
`STUDY PARTICIPANT MESSAGE`. It provides item positions, content previews, and
bounded claimed metadata (`id`, `sender`, `recipient`, `created_unix`, `authority`)
when the following line is JSON. These claims are attributed to the participant,
not promoted to original requirements or verified facts. A notice's presence
shows inclusion in that captured request, not comprehension or obedience. A
zero count covers **only that selected body and recognized encoding**, not all
messages ever delivered. Notice detection does not search nested replay/tool
text or infer links to a `study_message` event.

## Bounds and navigating omitted content

- `--preview-chars`: 0–4000 Unicode characters per preview, default 240.
  Each preview reports `chars`, `shown_chars`, `omitted_chars`, and its
  representation. Object previews are **JSON renderings, not raw bytes**; a
  clipped preview is not necessarily valid JSON.
- `--limit`: 1–100 entries per collection, default 20. `--offset`: nonnegative,
  default 0. Each page reports `total`, `omitted_before`, `omitted_after`, and
  `next_offset`. Repeat the command with that offset to navigate.
- Offset applies independently to the top-level collections, context collections,
  task positions, and notice inventory. Nested outcome/evidence/call-match pages
  start at zero. Select an invocation directly to apply offset to its focused
  outcome page. To inspect further nested detail, follow the exact source
  references; the CLI does not implement an arbitrary recursive page selector.
- `--item INDEX` directly previews one zero-based request item, independently of
  the inventory page. An out-of-range index in an available body is an error;
  an unavailable body reports `request_item_unavailable`. A scalar Responses
  `input` is item 0, with pointer `/input`.

These are preview/collection bounds, not a global output-byte or memory cap.
The journal and relevant blobs are read and JSON-parsed in memory. An unusually
large run can still need substantial memory. Use a small `--limit` for compact
inspection. Exact larger content remains available without increasing previews.

## Retrieve exact evidence

Every record reference includes:

- `record_id`, event type, and `recorded_links`;
- `record_source.path` and a half-open `byte_range` of the complete JSONL line;
- `retrieve_argv`, an argv array for `python -m mads logs RUN --event ID --raw`;
- for attached blobs, `payload.sha256`, `path`, `bytes`, and verification state.

For example, use the **HTTP record ID from `chain.wire_request`**, not a guessed
later request:

```sh
python -m mads logs RUN --event HTTP_RECORD_ID --raw > captured-request.json
# Existing exact export interface, when its causal compiled/context links exist:
python -m mads inspect RUN request --record HTTP_RECORD_ID --raw > captured-request.json
python -m mads inspect RUN request --record HTTP_RECORD_ID --out NEW_EXPORT_DIRECTORY
```

These write to your chosen destination, not the inspected journal. The decision
command itself does not create an export. For a blob, directly reading its
verified `payload.path` also retrieves the exact capture. The existing `request
--readable`, JSON pointers, and `--item` previews are renderings, not raw slices.

**For blobless records, `logs --raw` is a JSON reserialization**, not the original
JSONL bytes. Obtain the original record line using its returned coordinates:

```python
from pathlib import Path
# ref is a source reference from the decision JSON.
start, end = ref['record_source']['byte_range']
with Path(ref['record_source']['path']).open('rb') as source:
    source.seek(start)
    exact_record_line = source.read(end - start)
```

That is also how to retrieve exact manifest/control and invocation argument
source records. Parse the line to access `raw_arguments` or `result`; extracted
string content is distinct from its JSON byte encoding. Blob hashes are verified
when visited. File identities are observations, not interprocess locks.

## Live and invalid data

`Journal` reads one prefix of `events.jsonl`. The final unterminated line is
ignored and counted, even if it happens to parse; a later inspection can see it
once complete. `journal.observed_sha256` hashes the entire observed byte prefix,
including that tail. Byte ranges refer only to complete records. A live run can
end after a request, response, assistant, invocation, or outcome; missing later
stages are reported without borrowing future or older decisions.

An invalid selection/option, malformed **complete** JSONL line, or duplicate
record ID is a CLI error (exit 2), not a fabricated empty run. A successfully
emitted partial/unavailable JSON projection exits 0. This is not a run exit
status. Concurrently appended events do not enter an existing `Journal`
instance, but blob/identity-file reads are not an atomic filesystem snapshot.
The command assumes ordinary Store event schemas; it is not a general repair
utility for arbitrary structurally corrupt journals.

Existing `summary`, `timeline`, `accounts`, and `request` behavior is unchanged.
No aggregate dashboard, process polling, provider dispatch, or account mutation
is added.
