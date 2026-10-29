# Check catalog

Execution is explicit. `python -m mads check --list` is the executable offline
allowlist. Twelve checks run by default and eight are available by name. Four
additional scripts need recorded inputs or isolated environments and are run
separately. Use the runner or an explicit script command rather than glob-importing
the directory.

| Category | Scripts under `checks/` | Prerequisites and intent |
| --- | --- | --- |
| Default offline | context_tail, context_inspection, checkpoint, navigation, evidence_versions, evidence_delivery, retention_recovery, compact_receipts, jobs_sampling, review_affordances, inspection, workspace_snapshot | Existing 12-check default; local files/processes and literal protocol fixtures |
| Optional offline | boundaries, context_boundaries, context_candidates, mask_restoration, presentation, responses_protocol, responses_audit, export | Explicit names through the same runner; SDK mock transport, real local processes/Git, no provider calls |
| Special isolated environment | faults, detached_capture | Faults needs an unprivileged constrained filesystem; detached_capture refuses execution outside Docker; use network-disabled containers and guaranteed teardown |
| Local replay/template | completion_boundaries, qwen_template | Explicit recorded-response/template input; template probe additionally needs Jinja; not hosted-template proof |

`tests/test_correctness.py` supplies targeted offline fault injection for outcome
serialization, journal interruption, context correction, process cancellation,
UTF-8/diff/search presentation, fake transport, and CLI/manifests.
`tests/test_run_checks.py` covers the check runner. Unit discovery is limited to `tests/`.

The supported operator auditor lives in `mads/inspection/audit_journal.py` and runs
through `python -m mads audit RUN`. It remains independently implemented and can
optionally write review/output; it is not a regression-check script.

Runtime dependencies are pinned in `requirements.txt`, including directly used
`httpx`. No dependency installation occurs in the offline runner. The Docker
assignment launcher installs runtime requirements in the task container; host
launch/import uses only the standard library. The separate template probe needs
Jinja, which is not included in the runtime requirements.
