# One solver, one fresh review/repair owner

The supported flow for `python madsLoop.py`, `python -m mads run`, the shared `Agent`, and
`python -m mads assignment run` is one solver followed by one final reviewer.

1. The implementation session investigates and changes the software with the rich
   shared engineering SYSTEM and full direct tools. `finish(summary=...)` proposes
   completion. It is not an option to skip review or delegate the implementation.
2. The harness consumes that proposal once and starts one fresh same-model/provider
   session. The current workspace remains editable. The complete exact original task,
   every explicit later user message, exact predecessor summary, rich review assignment,
   current execution routes and context policies reach that session. Predecessor
   assistant/tool conversation, account, selected evidence and early notes are **not
   automatically replayed**. Historical paths remain optionally retrievable; source
   and tests can themselves carry inherited assumptions. Freshness is not independence.
3. The reviewer owns review **and repairs**. Its summary-only finish ends the run;
   no recursive reviewer, automatic return to the solver, reconciliation vote or
   all-green gate is added. The final workspace includes its edits. The two sessions'
   claims, provider/process outcomes, cleanup and export are recorded separately.

Both sessions retain ordinary filesystem, shell, network, editing, research,
execution targets, process control, evidence, context and verification tools.
Neither ordinary schema exposes `collaborate` or finish review-mode arguments.
This is a small orchestration policy, not a filesystem sandbox or a limit on the
amount of engineering work. The model is instructed not to instantiate coding
successors or build agent trees through other means.

## Configuration

```sh
# Ordinary run.
python -m mads run /repo --task /task.txt --run-dir /new-run
# Preserve later instructions as separate exact UTF-8 user messages.
python -m mads run /repo --task /task.txt --user-message-file /correction.txt --run-dir /new-run
# Optional explicit reviewer instructions.
python -m mads run /repo --task /task.txt --review-prompt /chosen-review.txt --run-dir /new-run
```

`MADS_REVIEW_PROMPT` or `--review-prompt` selects optional UTF-8 review instructions.
The course runner snapshots the chosen file into its writable staged `/agent` copy
and records its source/hash. No host-only prompt path is needed inside the image.
There is no public workflow selector; old `MADS_WORKFLOW` values cannot disable
review or enable removed orchestration. The optional `verification` tool records
and executes expectations anchored in original-task or later user instructions.
No planning or passing-test ritual is required by ordinary completion.

## Evidence and failure semantics

The root `solver-result.json` records the first exact proposed claim. `reviewer/`
contains the fresh input packet, source/task manifest, independent native journal,
process output and result. `reviewer/report.json` links pre/post change observations
and the actual child outcome. Root `result.json` separates `solver_outcome`, final
`agent_outcome`, `review_outcome`, `harness_outcome`, `cleanup_outcome`, and later
`export_outcome`. Root iterations count its own requests; child iterations are in
its outcome, and total tokens are accumulated. Course JSONL follows explicit
provider-fork links to project actual HTTP activity from **both** journals.

New live user instructions are checked after responses and again at finish. Calls
based on superseded input are skipped and the **same owner** continues with the new
instructions, not another agent. A message after the final accepted input boundary
is not retroactively applied to a completed run.

The predecessor stops making model requests once its proposal is accepted. Its useful
background jobs remain live through review. The reviewer inherits stable poll/send/
cancel handles, shared capture state and noncolliding new IDs; each observation is
archived in the controller's own journal and launch origin remains attributed. If
owned jobs/captures are still active at final finish, teardown is performed first and
its effects returned to this same owner for reconsideration. Shutdown can change files,
so a claim predating those effects is not silently accepted as final verification.
This conditional finalization is not an extra reviewer or test-success gate. A later
claim can honestly leave a failed settlement unresolved, with a separate failed harness
outcome. Completed groups are not signaled again after an accepted final claim. Host
cleanup still does not attest detached, remote or container-internal termination.

Provider retries retain the existing explicit transient-transport policy. A terminal
review/startup/capacity/process failure does not silently skip review, return to the
solver for a success narrative, retry through new agent chains or erase partial
edits. The root records failure and the workspace/evidence remain. A startup failure
before any child work has an empty completed child cleanup; incomplete actual child
cleanup is propagated and prevents attested final export. A failed model run whose
owned processes were cleaned can still export a **partial** patch. A reviewer's
honest report of unresolved behavior can end normally; that is a fallible claim,
not a declaration that every requirement is satisfied.

Automatic session resume is not claimed. To continue a terminally failed review,
an operator can start a new run with the current workspace, original task, later
instructions, and attributable handoff/evidence. That new run uses the same
solver/reviewer flow. Preserve the previous failure outcome instead of overwriting it.

## Checks and limits

`tests/test_reassessment.py` exercises the real Agent, tools, context and cleanup,
plus both native Provider protocols through mock HTTP. Cases cover exact task and
summary delivery, no old history/account, later instructions/restart, forbidden
model bypasses, one reviewer, edits despite interpreted red checks, startup/transport
and cleanup failures, live predecessor jobs, target/policy inheritance, unavailable
Git, normal CLI export and both-session Course projection. Low-level tests can exercise the reviewer loop directly without another transfer.
These are lifecycle checks, not evidence of model task-solving quality.

The full prompt inventory is [here](../mads/prompts/README.md). Real Qwen task runs,
source activation and independently adjudicated outcomes are separate study evidence.
