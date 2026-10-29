# Active engineering prompts and completion ownership

These are **loaded, editable runtime resources**, not optional skills that an agent
must discover. `mads.prompting.SYSTEM` includes `coding.md` and `runtime.md` directly
in every Agent system message. The normal course and general CLI run has two owned
sessions: implementation, then one harness-started fresh final review/repair owner.
Both use that shared core. `review-rich.md` is additionally a readable main **user
assignment** to the reviewer, followed by the exact predecessor completion report.
The complete original task remains a separate exact user message, followed by all
explicit later instructions. It is never replaced by the review prompt or summary.

An operator can pass `--review-prompt FILE` to supply other review instructions;
the flow stays solver → reviewer, and finish remains `finish(summary=...)`.
See [workflow documentation](../../docs/solve-review.md).

## Assembly inventory

| Source | Actual presentation / responsibility |
|---|---|
| `coding.md` | Shared SYSTEM: full-task scope, source/caller investigation, debugging, contracts, coherent implementation, detailed contract families, falsifiable checks, oracle quality, execution provenance, context and honest completion. Essential guidance is embedded, not only linked. |
| `runtime.md` | Shared SYSTEM: direct tools, execution and evidence semantics, task/correction continuity, source observer, fixed completion ownership. |
| `mads/workflow.py` | Session-specific SYSTEM notice: implementation or final reviewer. Normal schemas omit participant spawning and finish mode choices. |
| `mads/agent.py` | Appends actual paths; preserves original task and explicit later user messages; one first-finish transition, final reviewer result, cleanup and outcomes. |
| `mads/reassessment.py` | Same-provider fresh context; rich USER assignment + exact prior summary; current editable source, targets and context settings; historical evidence paths without automatic replay/account. |
| `mads/context.py` | Original task, later user instructions and active assignment survive restart and presentation masking. Working notes/retained historical evidence are labeled fallible data. |
| `mads/handoff.py` | Explicit operator continuation: byte-bound original task, fallible account/evidence, optional v2 later user messages. Not automatic predecessor history in the fixed reviewer. |
| `mads/runner.py`, `madsLoop.py` | Real course entrypoint uses the same Agent default and prompts, writable staged source, normal patch and Course JSONL export. |

Changing a resource on disk does not alter strings already imported by a running
session. Activate a new runtime when appropriate and inspect the actual outgoing
requests. `run-manifest.json` records resource snapshots and **loaded prompt string
hashes**, which are not necessarily equal to later disk contents.

## Design references

The shared guidance covers task scope, causal debugging, verification, context
management, and recurring contracts such as configuration, serialization,
streaming, migrations, public APIs and resource lifecycles. It was developed
with observations from the development tasks and applies across repositories.

References include Qwen Code's prompt assembly, OpenAI's Codex Prompting Guide,
Matt Pocock's diagnosing-bugs, Superpowers' debugging and verification skills,
Anthropic's autonomous-development debugging guide, and the Otter, RETRACE,
Chain-of-Verification and CRITIC research. These references inform the design;
performance is measured separately on completed task outcomes.
