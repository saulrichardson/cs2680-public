# Offline regression checks

`experiments/run_checks.py` runs a **reviewed, explicit allowlist** of local harness
regressions. It does not discover or execute `checks/*.py` indiscriminately: four
additional scripts require recorded inputs or isolated environments. The registered
checks need no provider, model, network service, credential, or controller.

The runner uses only Python's standard library. Running the harness checks needs
the harness's dependencies already installed in the chosen Python environment
(including the SDK imported by the compiler fixture, without making requests).
Use the harness's Python environment (validated here with Python 3.12) and a POSIX
system with local `bash`. The runner does not install anything. `--list` and
`--help` do not import the harness or launch checks.

## Usage

From the repository:

```bash
python experiments/run_checks.py --list
python experiments/run_checks.py --output .runs/offline-001
python experiments/run_checks.py --output .runs/navigation-001 navigation evidence_versions inspection
```

From **any working directory**, use an absolute script path:

```bash
python /path/to/harness/experiments/run_checks.py --output /tmp/harness-checks-001
```

Omit names to run the default set below; supply names to run only those checks, in
that order. `--list` prints names, descriptions, default membership and command
templates using the current interpreter and absolute script paths. It cannot be
combined with an output path or a selection. Check scripts are not CLI inputs:
there is no custom command, glob, shell expression, or live-check option.

`--output NEW_DIR` is required when running. Relative destinations are relative
to the caller's cwd, not the repository. **The destination must not exist** (even
an empty directory, file, or dangling symlink is refused). Every name, including
names late in the selection, is validated before any check launches or any output
parent is created. Unknown names, duplicates and an empty programmatic selection
are rejected. A missing `--output` produces a usage error, not a check's missing
artifact-argument traceback. Existing evidence is never reused or resumed; use a
new destination for a new run. Parents of a valid new destination may be created.

## Default allowlist

The twelve names below remain the default set, in this order. Eight optional offline
checks are also available by explicit name; see [check catalog](check-catalog.md). The script is exactly
`checks/<name>.py`. Every actual command has this form:

```text
<sys.executable> -u <absolute-repository>/checks/<name>.py [<unique-artifact-dir>]
```

| Name | Artifact-dir argument | Local coverage |
| --- | --- | --- |
| `context_tail` | Required | Tail/account layout and protocol fixtures |
| `context_inspection` | Required | Context inspection, retention and local jobs |
| `checkpoint` | None | Checkpoint effects and staged context adoption |
| `navigation` | None | Snapshot search and guarded edits |
| `evidence_versions` | None | Versioned evidence and immutable recovery |
| `evidence_delivery` | Required | First delivery and exact retention |
| `retention_recovery` | Required | Oversized retention and recovery |
| `compact_receipts` | Required | Compact receipts and exact content recovery |
| `jobs_sampling` | None | Process sampling, capture and cleanup |
| `review_affordances` | Required | Local stdin accounting and unsent-byte recovery |
| `inspection` | None | Read-only journal inspector and exact export |
| `workspace_snapshot` | None | Snapshot provenance and safe refusal |

Each check runs once in a **fresh Python subprocess** using `sys.executable`, with
cwd set to this development repository and `PYTHONPATH` set to its absolute path.
The runner passes an argv array (`shell=False`), with stdin disconnected. Spaces,
quotes and shell metacharacters in paths are not evaluated. Numbered per-check
directories have independent `home/`, `tmp/`, logs, and (when required) an
`artifacts` path. That artifact path is **not precreated**: these checks create it
themselves and some require it to be absent. Checks without that argument manage
their own temporary directories, which they may delete on completion; the runner
preserves their full logs but does not change that internal retention behavior.

The child environment is constructed from a short allowlist: a local executable
search path, fresh HOME/TMPDIR, harness PYTHONPATH, unbuffered/UTF-8 Python I/O, no
user site, and no bytecode writes. No ambient credentials, Python startup hooks,
`PYTHONOPTIMIZE`, or caller `PYTHONPATH` are forwarded. Assertions remain enabled.
The exact supplied environment is included in each summary row.

## Evidence and result interpretation

An output directory looks like:

```text
summary.json
01-context_tail/
    stdout.log
    stderr.log
    home/
    tmp/
    artifacts/...
02-context_inspection/...
```

Both logs are complete byte streams, written directly to separate files, not a
truncated console preview. They can contain non-UTF-8 bytes. Separate streams do
not establish cross-stream ordering. The console prints each completed check's
status, exit code and duration, followed by aggregate counts and the JSON path.

`summary.json` has `schema_version: 1`. It records the selected order, repository,
Python executable/version, output path, UTC start/finish times, monotonic elapsed
duration, aggregate `status`, `runner_exit_code`, `runner_error`, interruption
signals, and `checks`. Each check row records its **exact argv**, cwd, supplied
environment, artifact/log paths, status, PID, exit code (or explicit launch error),
timestamps, duration, and process-group cleanup observations. Durations include
cleanup; they are not CPU benchmarks. Finalized summaries also contain `counts`.

| Check status | Meaning |
| --- | --- |
| `pending` | Selected, not yet attempted (in-progress snapshot only) |
| `running` | Attempt underway; **not proof of success** |
| `passed` | Observed subprocess exit 0, no interruption, no reported cleanup error |
| `failed` | Observed nonzero exit or reported cleanup error |
| `launch_failed` | `Popen` failed; `launch_error` gives exception type/message; no PID or exit code |
| `interrupted` | Current attempt stopped because the runner received a signal; **not passing even if the child exited 0** |
| `incomplete` | Runner error prevented completion; see `runner_error` |
| `skipped` | Not launched/completed as a check because an interruption or runner error stopped the sequence |

A nonexistent script normally starts Python and exits 2: that is a **completed
failure**, not a launch failure. Negative subprocess exit codes denote signals
on POSIX. Null exit codes do not mean success. Failed and launch-failed checks do
not stop subsequent checks. Nothing is silently retried. Runner infrastructure
errors (e.g. log storage failure) stop the sequence instead of discarding earlier
evidence and pretending that later checks ran.

The JSON is written before launches and after completed attempts, using a flushed
temporary file and atomic replacement. While running, readers get a complete old
or new JSON document; it is not an atomic snapshot of the whole output tree or a
power-loss durability guarantee. A `running` summary left by an uncatchable kill
is incomplete evidence, not a passing result.

Exit codes: **0** all passed; **1** check/launch/recorded runner failure; **2**
usage/preflight/setup error; **130** SIGINT; **143** SIGTERM (in general,
128 + first received signal). Storage failure may also prevent saving a final
summary and produce a CLI error; inspect stderr and the last saved JSON.

## Interruption and safety limits

Ctrl-C or SIGTERM stops dispatching new checks. The runner preserves completed
results, marks the current attempt interrupted and remaining attempts skipped,
and saves a partial summary. Repeated signals do not abort cleanup. For the
current subprocess's **own process group**, cleanup sends SIGINT, waits up to one
second for Python `finally` blocks, escalates to SIGTERM for one second, then
SIGKILL if necessary and reaps the leader. Cleanup also runs after normal leader
exit to stop leftover group members from holding the logs open. The summary
records signals sent, cleanup errors and whether the leader was reaped. Orphan
zombies awaiting their parent are not executing processes.

This is a reviewed allowlist, **not a security sandbox or network firewall**.
The registered scripts must remain trusted and offline. Re-review source and
argument contracts before adding a check to the allowlist. The runner cannot
prevent modified code from opening a network connection or reading arbitrary
files. Environment filtering is not filesystem credential isolation.

Some harness fixtures launch their own process sessions and normally clean them
up in their own teardown. Intentionally detached descendants are outside the
runner-owned process group guarantee; use OS/container containment when stronger
isolation is needed. SIGKILL to the runner, a machine crash, or a full/unwritable
filesystem can prevent cleanup or an up-to-date final summary. There is no
automatic check deadline; interrupt a hung check. There is no automatic resume,
retry, provider-compatibility assertion, or official evaluation in this command.

## Testing the runner

```bash
# From the repository, using small local fixtures, not model/provider calls:
python -m unittest discover -s tests -v
```

The focused tests cover defaults/subsets and arbitrary cwd, exact argv/imports,
required unique artifact paths, full binary logs, preflight no-effects rejection,
nonzero and real OS launch failure followed by success, no retries, preserved
partial summaries, repeated interruption with parent/child group cleanup,
launch-boundary interruption, graceful interrupted exit 0, and normal-exit
leftover group cleanup. They use temporary local scripts and test-only Python
registry injection, not a custom-command CLI. Expected failing fixtures are
asserted as negative tests, not counted as harness regressions.
