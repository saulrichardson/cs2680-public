# Course Slurm adapter

[harvard.py](../adapters/teamharness/harvard.py) adapts a frozen team-harness package
to the course's Slurm execution environment. It prepares a transferable bundle,
submits an owned job, starts the packaged runtime, and retains the resulting
execution records. Course images, scheduler access, and machine configuration are
external inputs; this checkout contains no account inventory or qualified hardware
claim.

From `assignment4/`, prepare a bundle using explicit existing inputs:

```sh
python3 lab/adapters/teamharness/harvard.py prepare \
  --experiment /absolute/frozen-experiment \
  --screens /absolute/native-checks \
  --output /absolute/new-bundle
```

Transfer the bundle to the configured course environment and use the adapter's
`submit --help` output for submission arguments. A native-check directory contains
`screens.json` entries with `name`, `fixture`, and `seconds`, plus each fixture's
`source/`, `task.txt`, and `check.py`. Checks report integer `tests_total` and
`tests_failed` fields as JSON. A native check is distinct from the original
benchmark grader.

Submission records the intended job before dispatch. If dispatch completion is
uncertain, reconcile the recorded identity with the scheduler before submitting
again. Runtime deadlines, source snapshots, and planning/completion policy come from
the captured package. Use the [adapter guide](../adapters/teamharness/README.md) for
the surrounding package and observation lifecycle.
