# Assignment 2 CLI and log compatibility

The shared `Agent`, tools, context compiler and `Provider` still serve Course and
Responses routes. Course (`qwen3.6-35b-a3b`) remains the default.

## Entrypoints

```sh
python madsLoop.py -p "original problem statement" --log /app
# Existing file-based interface is also supported:
python madsLoop.py /app --task task.txt --run-dir /output/run --log
```

`--log` is off by default. With it, the default destination is
`./madsLoop_logs/`; `--log-dir PATH` overrides that location. The `-p` interface
prints diagnostics/outcome to stderr, not mixed-schema stdout. Native evidence is
always recorded, even without `--log`. If `--run-dir` is omitted, a fresh unique
directory is chosen; runtime evidence is staged outside the target repository.
When invoked from the target cwd, logs are published there only after solving.
Use a separate invocation cwd to avoid adding those artifacts to a later patch.

`evaluation_scripts/run_task.sh INDEX` is the editable Docker adapter. It accepts
`MADSLOOP_REPO` and `TASKS_JSON`, runs setup using a selected Python >=3.10, invokes
logging, exports `model_patch_<instance_id>.diff` at the assignment root, and
copies logs to `madsLoop_logs/<instance_id>/<attempt_id>/`. Each run has a unique
native evidence directory. A new attempt removes a stale patch for that instance
before launch. Cleanup/export failure is not silently promoted to success.

`docker_env.sh` installs harness requirements for `MADS_AGENT_PY` or a discovered
Python >=3.10; it bootstraps pip if necessary. The runner also bootstraps a modern
Python with uv when the task image lacks one. Task tests retain their own image
environment. For standalone setup, run the agent with the **same interpreter**
selected for installation; setup does not replace the image's default `python`.

The adapter does not require our customized `run_all.sh`, bundler or controller.
The original downloaded `run_all.sh`, `make_patches.py` and `evaluate.sh` can be
used unchanged with the editable adapter and the ordinary official evaluator.
The Docker daemon, task images, public evaluator assets and host-side dependencies
remain external prerequisites.

## Exact course schema, separate evidence

The course file contains only `timestamp`, `event` and the handout's fields:

| Event | Additional fields |
| --- | --- |
| run_start | model_id, workdir |
| api_request | iteration, prompt_tokens, completion_tokens, total_tokens |
| api_retry | iteration, error, backoff_s |
| tool_call | iteration, tool_name, arguments |
| tool_result | iteration, tool_name, result, is_error |
| run_end | reason, num_iterations |

Each course trace has a separate `run-*.manifest.json`. Full native evidence is
under `evidence/run-*/`, including every parent/child journal and byte-addressed
blob. Preserve **both** files and that evidence tree. Native `events.jsonl` files
are not course traces; do not glob all nested JSONL files as though they shared a
schema. `native_root` in the manifest is relative to the manifest directory, so a
copied `madsLoop_logs` tree is self-contained. Original container paths are also
retained rather than rewritten inside historical records.

Important reconciliation rules:

* Global iterations identify logical requests across explicit `Provider.fork`
  links. Child requests/usage are counted; unrelated journals in copied source
  workspaces are not. Transport retries share an iteration, but each actual
  attempt gets an `api_request` usage row. `num_iterations` counts logical requests,
  not attempts, across that tree.
* Usage is joined from the **actual** response payload, never estimated from
  character counts or reconstructed from a parent-only total. Missing usage is
  JSON `null`, not a fabricated zero. Detailed provider usage and response/status
  links remain in the manifest/native evidence. Responses `input_tokens` and
  `output_tokens` map to the course prompt/completion names.
* `tool_call` records native invocations. `tool_result.result` is the exact string
  in each recorded model-facing HTTP body, including masks, restored presentations
  and rejected-call historical notices. It is **not** reconstructed from a fresh
  tool/file read. Every actual presentation is recorded, even if previously sent;
  those repetitions are not additional tool executions. Context restart naturally
  removes presentations from later requests. The manifest identifies the sending
  request, body hash/JSON pointer, native invocation and receipt for each result.
* A tool that was never fed back (notably terminal `finish`, or work followed by a
  capacity/error stop) has no invented model-facing result. Its full receipt is
  native, and the manifest lists `unpresented_invocations`. HTTP dispatch evidence
  does not attest that the server consumed it.
* `is_error` reflects a failed/rejected harness tool operation. A successfully
  observed shell command with nonzero exit is not automatically a tool failure;
  its exact process result remains in the string/native receipt.
* `run_end` maps completed to `done`, operator iteration limits to
  `self-imposed limit`, and interruptions/capacity/other errors to `error`. Cleanup,
  agent claim and export are still separately recorded natively. Course fields
  alone cannot reconstruct every context/provenance detail; the separate evidence
  resolves that limitation without adding nonstandard keys.

Projection is finalized after the run, not incrementally streamed. SIGKILL,
preflight argument errors, failed storage or failed export can leave no course
file; native evidence is preserved when available. Completed startup failures
are recorded as an error run with zero iterations. An interrupted/inconsistent
journal or corrupt referenced blob causes visible projection failure, not guessed
content. For preserved, valid native runs using this fork-link format:

```sh
python -m mads.course_logs /path/to/native-run /path/to/run.jsonl
```

Do not claim this exports all children from older journals lacking fork links.

## Optional relay endpoint

`MADS_BASE_URL` overrides only the selected route's endpoint; protocol, key
environment and default model do not change. The Docker adapter forwards this
variable by name, just like the key variable. Without it Course uses
`https://api.cs2680.com/v1`. A Course relay capability goes in `CS2680_API_KEY`,
never in source, command arguments or the URL. The base URL is logged: it must be
non-secret. Keep secret values out of prompts, command arguments and logs. Authorized clients
may load credentials from the operator-provided readable files or environment.
A relay is optional. Authentication and exhausted-quota errors stop the run.

## Compatibility checks

`tests/test_course_compat.py` uses fake HTTP responses to check wire-string
equality, context changes, child usage, retries, Responses, CLI, relocation and
routing. These checks cover the adapter and logging contract; task-solving
results require separate evaluation.

## Local batch selection and packaged tools

`TASKS_JSON` selects the same task mapping for individual launches, batch counts
and manifests, patch bundling, evaluation input and summary. Relative paths resolve
from the invoking directory. Without an override, the supplied ten tasks remain
the default. `TESTS_JSON` optionally selects matching evaluator metadata; it is
never passed to the solver. Missing evaluation records stop evaluation explicitly
while leaving generated patches available.

The staged `/agent` includes the CLI's read-only inspector, journal auditor and
reviewed offline check catalog with its task-independent fixtures. The staging
list derives from that catalog; assignment diagnoses and evaluator tests are
excluded. The source remains writable.

The host launcher handles SIGTERM/SIGINT by removing its owned container and
finalizing an interrupted receipt, preserving any already completed patch export.
Its previous signal handlers are restored afterward. SIGKILL and machine loss
cannot run cleanup; the receipt must not be read as evidence that either occurred
cleanly. Launcher outcome, agent outcome, container cleanup and export are separate.
