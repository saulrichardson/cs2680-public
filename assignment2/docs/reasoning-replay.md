# Optional plain-reasoning replay

`reasoning_replay` is a reversible **presentation policy**, not a deletion or a
reasoning-effort setting. The default is `all`, preserving existing native replay.
It is available to the model through `context_configure`, with no automatic
workflow or requirement to use it. Whether it improves task solving is an
empirical question; mechanical replay tests do not establish a reliability gain.

| Value | Native Chat assistant replay |
| --- | --- |
| `all` | Keep every surviving exchange's string `reasoning_content` and `reasoning` fields. |
| `latest` | Keep those fields only on the newest retained exchange. This means the newest exchange, **not** the newest exchange that happens to contain reasoning. |
| `none` | Omit those fields from every retained exchange, including the newest. |

The projection is deliberately narrow:

- `reasoning_details`, including structured/signed/encrypted provider data, is
  unchanged. All native Responses `responses_output` items remain unchanged by
  this policy, including reasoning, summaries, encrypted content, messages and
  function calls. Existing invalid-call/envelope handling is independent.
- Assistant content, calls, results, notices and ordinary tool effects are not
  removed or reexecuted. Tool-output masking and hard-cap paging still have their
  existing, separate scope. `keep_recent` controls body masking, not reasoning
  replay; it cannot override `latest` or `none`.
- Raw returned responses, native assistant journal records and the assistant
  objects in retained Context groups are not rewritten. Switching back to `all`
  restores the surviving plain fields from those groups without another model
  generation or tool execution. Exchanges dropped by `context_restart` do not
  return automatically.
- This is **not an erasure/privacy filter**. Exact retained evidence, explicit
  `history_read` results, working-account text, assistant content and reasoning
  quoted in other fields remain visible as before. Native evidence remains
  retrievable even when its plain reasoning is omitted from automatic replay.

## Select or change the policy

Within a running session:

```json
{"reasoning_replay": "latest"}
```

Pass that object to `context_configure`. Pass `{"reasoning_replay":"all"}` to
restore surviving plain reasoning. The call is staged until the end of the
response batch; multiple configurations merge in call order and are fit-checked
with any staged restart. Invalid values reject. If the combined context does not
fit (for example, restoring large reasoning), the previously selected policy and
history selection survive. Ordinary effects, including an account write, are
not rolled back or rerun. Inspect `context_change` and `applied_context`.

From the ordinary agent entry point:

```sh
python madsLoop.py /absolute/workspace --task /absolute/task.txt \
  --run-dir /absolute/new-run --reasoning-replay latest

MADS_REASONING_REPLAY=none python madsLoop.py /absolute/workspace \
  --task /absolute/task.txt --run-dir /absolute/another-run
```

The CLI flag overrides `MADS_REASONING_REPLAY`; missing values default to `all`.
Both CLI and environment values are validated, including during `--dry-run`.
The Assignment Docker runner forwards `MADS_REASONING_REPLAY` by environment
name to the same CLI. This setting is distinct from the provider's `--reasoning`
effort flag / `MADS_REASONING`.

Programmatic callers can pass `reasoning_replay='latest'` to `Agent` or `Context`.
An explicit Agent keyword wins over the environment; without it Agent reads
`MADS_REASONING_REPLAY`. Direct Context construction defaults to `all` and does
not read environment variables.

The fresh final reviewer inherits the parent's **currently committed** replay policy, not the value used at initial launch. A configuration
still pending in the same response has not committed yet. Child contexts remain
fresh: this inheritance does not copy parent history, account or selections.

Native handoff bundles intentionally carry **no context policies, provider
configuration or native conversation history**. A successor seeded from such a
bundle must select its policy as a new launch option (CLI/environment or Agent
keyword). Importing a bundle does not infer the
source run's policy, and selected exact evidence in a bundle is not filtered.

## Inspect what was actually sent

`context_status`, fresh runtime facts, configuration/restart acknowledgments and
context manifests expose `reasoning_replay` and `reasoning_omission`. The latter
states its scope and counts omitted **fields** and **exchanges**, with exchange
IDs. These are field counts, not estimated token savings. Status refers to the
last finalized request, before any pending sibling calls.

Projection changes on old exchanges receive `exchange_representation` records;
those records identify `reasoning_replay` and `omitted_reasoning_fields`. Raw
assistant records remain unchanged. Finalized request blobs and recorded HTTP
bodies show the actual projection, while the course log continues to index
literal tool-result strings sent on the wire, not invented executions.

The independent `mads/inspection/audit_journal.py` validates the declared projection against
selected exchange → native assistant source identities, not any earlier matching
message. It checks omission counts/IDs/scope, exact remaining fields, representation
metadata, and unchanged structured/Responses data. Older journals without policy
metadata retain the `all` interpretation. Literal compiler fixtures without native
assistant sources remain explicitly limited, not falsely certified. Negative tests
rehash modified journals and reconcile telemetry before checking that unauthorized
omissions, source substitution and field changes are still rejected. The auditor
does not attest the provider's internal prompt template or launch-option provenance.

The focused tests use the real shared Agent/Context, OpenAI SDK and fake HTTP
transport, exercising omission/restoration, staging and rollback, real ordinary
effects, exact evidence, child inheritance, CLI/environment selection, and
unchanged structured/Responses data. They do not establish acceptance by every
live provider or improvement in Qwen's problem solving. Activate edited source
through a successor/reload before attributing live behavior to this policy;
changing a file does not replace an already loaded Context implementation.
