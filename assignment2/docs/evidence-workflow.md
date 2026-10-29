# Source navigation and evidence inspection

The shared tools support source navigation, version checks, context retention
and inspection of saved requests. They operate on captured evidence; the model
assesses task correctness and completion.

## Find an interface without reconstructing its coordinates

```json
{"path":"src/reducer.py","query":"def reduce","context_lines":8,"max_matches":10}
```

Pass this to `search`. It searches **one UTF-8 file or saved artifact**, literally by
default; `regex:true` uses Python regex. It reports the first match on each matching
line, not all matches or cross-line matches. Use ordinary Bash for directory discovery.
The captured source is immutable. Results include line/byte coordinates, original
snippet text, and exact `read`/`retain` actions. Pagination follows that snapshot even
if the live source subsequently changes. Continuations carry the explicit original
`source_record`; pass it unchanged with its immutable path so later pages retain the
live-source link for `evidence_check`. A standalone blob read does not guess that link.
Absence covers only the reported search scope.
Output bounds affect display, not capture; inspect a saved page if its display is cut.
A search-page record links to the whole source observation rather than pretending its
JSON report is the source file.

Use an observation's SHA256 with `edit(expected_sha256="...")` or
`write(expected_sha256="...")` when editing from a possibly old snapshot. A mismatch
rejects before writing. `write(expected_sha256="missing")` guards file creation.
Receipts identify before/after content hashes. This is an observed-version check,
**not an interprocess compare-and-swap**: external writers may race after checking.
Existing unguarded calls are still supported; mode and symlink-target behavior is unchanged.

## Compare evidence with current bytes deliberately

```json
{"record_ids":[42,57]}
```

Pass this to `evidence_check` for up to 20 records. It compares whole-file observations
from `read`, or explicit source links from `search`, with the current resolved path.
Each item reports `same`, `changed`, `missing`, `unavailable`, `unstable`, or
`no_file_source`, including hashes and old/fresh immutable read actions where available.
It does not modify source files or silently replace retained evidence. Anonymous blob
reads, process output and diffs have no inferred live-file baseline. A matching hash
means sampled bytes match, not that an interpretation is correct, that a test covers
a requirement, or that the repository was observed atomically.

Use this when a plan depends on possibly changed source; do not treat it as an automatic
truth or stale-note detector. Source version checks cannot establish that a diagnosis
was ever correct. Preserve the observation, interpretation and unresolved question separately.

## Reconcile the account and replace spent context in one call

`context_restart` can optionally replace the single working account:

```json
{
  "account":"Deliverable: ...\nCompleted: ...\nObserved failure: ...\nRevised explanation: ...\nNext action: ...\nOpen requirements and references: ...",
  "keep_records":[{
    "record_id":42,
    "part":"payload",
    "view":{"bytes":[120,950]},
    "purpose":"Reducer interface for the next edit"
  }]
}
```

Replace contradicted diagnoses and obsolete next actions, rather than appending a
completion paragraph beneath them. The account is still agent-authored and unverified;
original requirements remain authoritative. `purpose` is an optional nonempty agent-authored
reason beside the **unchanged exact excerpt**, not a summary replacing its bytes or a
semantic relevance decision. It remains with the selection through acknowledgments,
status and manifests. A pointer alone is retrieval, not retained content.

Important effect boundaries:

* Invalid arguments, selection identities/ranges and duplicate restart are rejected
  before the optional write. `expected_account_sha256` requires `account` and can guard it.
* The account write is an ordinary file effect. End-of-batch context fit is separate.
  If fit fails, previous policies/selections survive but the successful account write
  and other effects are **not rolled back**. Do not repeat successful effects blindly.
* Later sibling writes can supersede that account. Check `effects`, `account_update`
  and `applied_context.account_read`, which identifies the actual end-of-batch sample.
* `keep_records` omitted preserves selections and purposes; `[]` clears them; a list
  replaces them. The current native exchange stays paired; jobs and files persist.

After a successful restart, perform the retained next action. Retrieve a specific gap
rather than reflexively rereading every file. Changed files or uncertainty can justify
rereading. The interface makes this possible, but cannot guarantee model uptake.
See [context lifecycle](context-lifecycle.md) for masking, capacity and native-pairing rules.

## Inspect actual experiment requests (without provider calls)

From any cwd, invoke the helper by absolute path:

```bash
python /path/to/harness/mads/inspection/inspect_run.py /path/to/run summary
python /path/to/harness/mads/inspection/inspect_run.py /path/to/run timeline --last 20
python /path/to/harness/mads/inspection/inspect_run.py /path/to/run accounts
python /path/to/harness/mads/inspection/inspect_run.py /path/to/run request --record latest --out /new/export
```

The run can contain `journal/` directly or under `run/`. The helper verifies referenced
blob hashes, tolerates an incomplete final line in a live journal, reports that scope,
and bounds raw argument previews while retaining exact record pointers. Request export
preserves the actual body, manifest and injected account. Native encrypted reasoning is
opaque; the helper inventories item types/IDs without interpreting it. Source/task identities
and recorded usage are available where the run has them. It does not repair the journal.

**These are inspection exports, not resumable continuations.** Exact replay would also
require filesystem/process state, retained native history, provider continuation and
policies. A fresh task with reconstructed notes must not be described as exact replay.

## Freeze a stopped solution before independent checks

```bash
python /path/to/harness/experiments/snapshot_workspace.py /stopped/source /new/copy > /manifest.json
```

Ensure the source is quiescent first. This helper compares before/after/copy inventories
of bytes, modes, directories and relative links, records a tree identity, rejects unsafe
links, special files and existing destinations, and cleans up its staging directory on
failure. It is not an atomic filesystem snapshot or process freezer. Serialize destination
creation. A detected change fails rather than silently manufacturing equivalence. Keep the
original stopped run intact; run mutating evaluation tools on the copy.

## Checks and limits

Focused offline scripts are `checks.navigation`, `checks.checkpoint`,
`checks.evidence_versions`, `checks.inspection`, `checks.workspace_snapshot` and
`checks.jobs_sampling`. Run them from the harness root with `python -m checks.NAME`.
Other checks may require artifact-directory arguments or dispatch live provider requests:
do not glob and execute every script. Failure and rejection fixtures are negative tests,
not evidence that live task failures are acceptable.

The process sampling fix deliberately reports a conservative `running` state when a
leader exits during descendant enumeration. It avoids combining an earlier descendant
sample with a later leader poll and incorrectly waiting for capture. Capture completion
and asynchronous stdin-writer error reporting remain independent observations; tests
wait for the latter without resending accepted input. Detached processes remain outside
the ordinary process-group cleanup guarantee.

No provider adapter, grader, automatic completion gate, semantic summary, second account,
mandatory schema or automatic source-watching loop is introduced by these changes.
