# Explicit fresh-session handoff

A handoff is **not native resume**. The original task remains authoritative.
The account is explicit, fallible operator/agent-authored knowledge. Selected
journal excerpts are historical data, not instructions or invented assistant
turns. A bundle transports bytes and claimed provenance, not proof of their
truth, authorship, relevance, or the successor's understanding.

## Export, validate, and inspect (offline)

```sh
python -m mads handoff export /path/to/source-run /path/to/new-bundle \
  --task-file /path/to/original-task.txt --account-file /path/to/reconciled-account.md \
  --selections /path/to/selections.json
python -m mads handoff validate /path/to/new-bundle
python -m mads handoff render /path/to/new-bundle > /path/to/handoff.txt
```

`selections.json` is a JSON list in the normal exact-retention format:

```json
[
  {"record_id": 314, "part": "payload", "view": {"bytes": [0, 1024]},
   "purpose": "Interface needed for the next edit"},
  {"record_id": 322, "part": "payload", "view": {"lines": [1, 12]}}
]
```

These IDs and ranges address the **source journal**. `part` defaults to `payload`,
`view` to `full`; explicit `part: "record"` exports record JSON, still only as
historical text. Copy/narrow a receipt's `output.retain`, not its receipt ID.
Bytes are zero-based, half-open; lines are one-based and inclusive. As with core
retention, a line end past EOF is tolerated, but clipped byte ranges, empty
selections, and ranges splitting a UTF-8 code point are refused. An empty
selection **list** is valid. Export requires the complete selected bytes; it
never substitutes a summary or a clipped preview.

The bundle contains `original-task.txt`, `account.md`, `evidence-NNNN.txt`,
`manifest.json`, and derived `presentation.md`. The manifest records each selected
source interval, file size/SHA-256, source record/part and provenance, source
journal head and partial-tail status, optional purpose, and fallible account
authorship. It does not copy the whole journal, native request state, provider
handles, jobs, or workspace. Export validates before creating its destination
and refuses an existing destination. Task/account bytes, including CRLF and
non-ASCII UTF-8, are preserved exactly.

Validation checks schema shape, required source/entry metadata, duplicate JSON
keys, safe unique filenames, regular non-symlink members, source interval and
size consistency, selectors, UTF-8, hashes, and explicit unverified account
labels. Files are captured once per load; rendering and import use those captured
bytes rather than reopening members after validating them. The hashes detect
byte inconsistency, **not forgery**: an operator can author a self-consistent
bundle. Source journal paths are attribution, not live local paths.

`render` remains a static compatibility presentation. If a launcher appends its
output to the task, those excerpts
become permanent task text and **cannot** be released by `context_restart`.
For releasable seeding use one of the following activation paths instead.

## Shared core API and CLI activation

The public shared API is:

```python
# Construct a FRESH Agent with the ordinary configured Provider and Store.
receipt = agent.seed_handoff(bundle_path)
outcome = agent.run()                     # unchanged shared loop
```

Call before any compile, run, or journal/tool activity. By default the bundle's
original-task bytes must equal `agent.task.encode('utf-8')`. A mismatch is an
error; there is no automatic replacement, normalization, or task rebinding.

Launch through the normal CLI, with a new/empty run directory outside the workspace:

```sh
python -m mads run /path/to/workspace \
  --task /path/to/original-task.txt --run-dir /path/to/new-run \
  --handoff-bundle /path/to/new-bundle --transport-retries 0
```

Use the normal provider configuration/approved route; the handoff has no provider
or credential configuration. Add `--dry-run` to validate the bundle/task and
configuration without creating an Agent, importing records, or sending a request.
A dry run is **not** a capacity check or a full request execution. The core API
and CLI make no provider calls during validation or import.

For a launcher with a wrapper-expanded task, the API also accepts:

```python
receipt = agent.seed_handoff(
    bundle_path,
    original_task=original_task_path.read_bytes(),
    initial_account=separate_seed_path.read_bytes(),  # optional
)
```

`original_task` must be exact UTF-8 **bytes**, equal to the bundle task and present
verbatim in `agent.task`; the full wrapper task stays unchanged. The journal
records both hashes and the explicit binding mode. Do not pass a different task
just because it seems semantically similar. If a separate `initial_account` is
supplied, only empty bytes or bytes identical to the bundle account are accepted.
Any other value is rejected, with no precedence rule: reconcile and re-export
before launch. A newer seed must not silently overwrite/be overwritten by an
older bundle account.

### Import records, attribution, and release

A successful import creates local `handoff_manifest`, `handoff_task`,
`handoff_account`, `handoff_evidence`, and `handoff_import` records with immutable
payloads. `handoff_import` and the returned receipt map source selections to
**new local record IDs**. Local causal links point to the local manifest; source
IDs and coordinates live under `foreign_source` and are not treated as local
causal links. The original source manifest and bytes remain locally retrievable.

Only imported evidence is added to `Context.records`, as local `payload/full`
selections. It appears in the first compiled and dispatched Chat or Responses
request using ordinary exact-evidence presentation, including historical-data
labels and foreign-to-local provenance. The original task remains a separate
user task message. The account is written to the run's `Context.working` file
(`memory/working.md` under the run directory), not a workspace `working.md`. Its
snapshot/tail/status labels retain operator/unverified import attribution while
its bytes equal the seed; a reconciled changed account uses ordinary agent labels.

For example, if source record 314 bytes `[900,1200]` was imported as local record
4, the whole imported payload is **local** record 4, bytes `[0,300]`. Do not reuse
314 or `[900,1200]` as local selectors. Use the receipt mapping, `context_status`,
or `history_search(query="", event="handoff_import")` to find local IDs. Source
`part: "record"` text also becomes a local **payload**, not a native event.

Ordinary controls now apply:

```json
{"account":"Reconciled current state...", "keep_records":[], "keep_recent_exchanges":0}
```

This `context_restart` releases the imported selections and replaces completed
investigation replay; it does not remove the original task, delete the local
journal, or imply that evidence became irrelevant. Omitting `keep_records`
preserves selections. A nonempty list replaces/narrows them using local IDs.
The current exchange remains paired. Evidence retrieved again in a recent tool
exchange can still appear through that exchange until it too is released.

After release, `history_read(record_id=4, part="payload", range="full")` retrieves
the exact imported bytes and provenance from the **local** Store, even if the
bundle and source run are unavailable. Use ordinary `context_restart` to retain
them again. Imported source file observations are not claims about current local
workspace files (`evidence_check` reports no local file source).

## Guards and limitations

- Only a fresh unused Agent/Store/Context can be seeded, once. Existing memory or
  run manifest, journal (even empty), blobs, groups, selections, compile state,
  job activity, iterations, completion, or edited account are refused. Context
  construction no longer erases an existing account. This is not a disk-resume API.
- Ordinary validation/task/account/freshness failure has no import effects:
  no partial journal records, account writes, or Context selections. Agent
  construction itself may already have created an empty account and run manifest.
  Persistence errors or interruption after validation are **not transactional**;
  records/effects may remain, and that Agent refuses another seed. Inspect and
  use a new run, never blindly retry uncertain writes.
- Import does not pre-fit a request. Exact retained evidence is never silently
  clipped. The normal compiler checks capacity before any dispatch; a too-large
  fixed context blocks with no API request. Normal explicit account recovery
  (previous/absent snapshot with disclosure) still applies if the account does
  not fit. Inspect the actual finalized context, not just import success.
- There is no authenticity signature, native conversation resume, transferred
  provider state, workspace transfer, job resurrection, automatic claim
  reconciliation, or automatic evidence relevance/retirement policy. Workspace
  snapshots are separate launcher inputs. Retention/presence is not comprehension.

## Offline verification

```sh
MADS_TEST_ARTIFACTS=/tmp/new-seed-fixtures \
  python -m unittest discover -s tests -p 'test_handoff*.py' -v
python -m unittest discover -s tests -v
```

Tests use the actual Agent loop/compiler and Provider SDK with local
`httpx.MockTransport` for both request protocols: exact CRLF/UTF-8 payloads,
first-request attribution and task separation, local retrieval after source/bundle
removal, retain/narrow/release/re-retain, unchanged task, native call/result pairing
and Responses reasoning items, invalid manifests/no partial context state,
conflicting seeds, CLI activation/dry run, driver pause/inbox, failure reporting,
usage/finish and real owned-child cleanup. No model successor or network/API
service is launched.
