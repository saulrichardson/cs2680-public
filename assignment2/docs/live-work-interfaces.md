# Live work and review interfaces

These are optional shared interfaces, not correctness authorities or completion
requirements. They use the ordinary Store, native protocol adapters and journal.

## Compare the supplied task and actual changes

```json
{"paths": ["src/module.py", "tests/test_module.py"], "base": "HEAD", "output_chars": 18000}
```

Pass this to `review`. With Git, it resolves `base` to a commit and captures the
staged **and** unstaged tracked diff, status, and separate untracked-file
snapshots. Without `paths`, Git coverage is the whole repository. Paths are
literal, not Git glob patterns; paths outside the workspace are rejected.
Ignored files are excluded. At most 20 new files are expanded, 16,000 characters
per text file; exact snapshots and omitted-file names remain retrievable. Binary
files have explicit read pointers, not lossy text renderings.

A workspace without usable Git still gets the exact initial task. Specify file
paths to get current snapshots; no baseline or diff is invented. A no-Git call
without paths prominently returns **TASK ONLY** and a suggested follow-up.
Directories do not trigger an implicit scan. An invalid base in a valid Git
repository remains an error.

The initial task is not a summary and is not a substitute for subsequent user
corrections. Review runs no tests. Its files and Git state are sampled
sequentially, not atomically. Read the receipt's coverage and `next_read` before
assuming all changes were displayed. `review` can be retained/retrieved through
ordinary journal evidence tools. It deliberately does not decide whether an
implementation is correct.

## No-change writes and edits

An identical-content `write`, or an `edit` whose replacement leaves the file
identical, validates the ordinary hash/match preconditions but does not replace
the file. The receipt says `effects: none`, `changed: false`, and **No change**;
before/after hashes are equal and `replacements` is zero. Edit additionally
reports `matched_occurrences`. A missing file written with empty content is a
creation, not a no-op. A failed precondition is still an error. This avoids
misreporting an empty diff as an effective repair; it does not infer intent.

## Inspect recent run activity

```sh
python -m mads inspect /path/to/run activity --last 8
python -m mads inspect /path/to/run activity --last 8 --after-record 120
python -m mads inspect /path/to/run decision --record 147 --limit 8
```

`activity` gives literal assistant content/reasoning where available, tool calls,
and associated recorded outcomes. It preserves native Chat versus Responses
shapes: encrypted reasoning stays opaque. Output previews are bounded; each has
an exact journal/blob pointer. A captured-output preview is **not necessarily**
the selection delivered to the model. Use `decision` or `request --view wire`
to establish actual request contents.

The observation includes the journal prefix hash/head, incomplete-tail byte
count, latest request/response/end pointers, and `next_after_record`. It is not
a liveness claim or an atomic process snapshot.

`--after-record N` keeps a turn if its assistant, invocation, or any associated
result has an ID greater than N, including a late result for an older call.
Filtering happens before the `--last` bound (1..20). N equal to the observed head
is valid and can return no turns; 0 is valid for an empty journal. N beyond the
observed head is an error. `eligible_turns` and `omitted_turns` expose truncation.
**The next-head cursor is not lossless pagination.** If turns were omitted,
inspect the older cursor with a larger bound or use `decision`/exact pointers
before advancing. This is a small activity view, not a replacement journal.

## Frozen runtime versus editable source

A source edit does not change the running interpreter. Freeze and start a new
runtime to activate shared changes. Fresh launches or file handoffs are not
exact native replays. Preserve runtime, prompt, tool schema, original task,
workspace, provider settings and launcher identities separately.

Launchers that boot with `PYTHONPATH=/runtime` may opt into the explicit
[boot-only Python path contract](launcher-boot-pythonpath-contract.md).
Subprocess tests must still target the edited source; check `module.__file__`
when import provenance is uncertain. Don't compare two implementations by
reloading the same cached module under two variable names.

## Mechanical checks

```sh
python -m unittest discover -s tests -p 'test_review.py' -v
python -m unittest discover -s tests -p 'test_unchanged_edits.py' -v
python -m unittest discover -s tests -p 'test_activity_inspection.py' -v
python -m unittest discover -s tests -p 'test_boot_pythonpath.py' -v
python -m unittest discover -s tests -p 'test_wire_improvements.py' -v
python -m unittest discover -s tests -v
```

Wire checks exercise the real Agent/Provider/Tools with mock HTTP transport in
both native protocols; they establish client compilation and receipt fidelity,
not hosted model behavior or task correctness.
