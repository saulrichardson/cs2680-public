# Main-runtime AWS preparation

These helpers stage and validate the maintained persistent-main package. They
reuse the existing fleet controller, collector and termination owner. Preparation
does not provision resources, reserve money or issue model requests. Already
frozen campaigns remain unchanged.

## Local package mechanics

```sh
python3 lab/adapters/teamharness/aws_preflight/validation_runner.py \
  --mechanics-only --repository /absolute/checkout \
  --package /absolute/package --output /absolute/new-evidence
```

The package must explicitly declare `workflow: main`. Historical packages require
their frozen implementation. The checker uses `check_main.py`, verifies its
interactive grades, fresh review, exact original-base replay, checkpoint fallback,
selection and course-owned job cleanup, and confirms unchanged input inventories.
Keep evidence outside repository and package inputs.

A successful receipt records `mechanics_passed: true` separately from
`full_preflight_passed: false`, `ready_for_original_runner: false` and
`live_model_validation: not_run`. It does not run the native unit suite or claim
model quality. Those observations must come from their own actual executions.

## Fleet source and host boundary

`freeze_validation.py` takes the maintained repository, reviewed fleet source,
pristine route source, built package and a new absent output directory. It binds
source/package inventories, embedded runtime bytes, route identity, pinned task
manifest, unchanged controller and preserved original host runner. Credentials
are never copied into the validation bundle. The old staged Qwen probe is no
longer copied or executable from this maintained path.

`launch.py --profile preflight` owns later admission and dispatch. The host wrapper
runs the native suite and packaged main check and requires Linux evidence.
**Live main-workflow validation and original course handoff are not implemented.**
After successful mechanics, the wrapper reports that boundary explicitly and
stops before Qwen or the original task. Launching it does not bypass that missing
implementation or establish readiness. A future live path must preserve original
requests, candidate/grading identities and the existing collection/cleanup owner.

Validation failures retain a separate infrastructure result and evidence archive;
they are not graded solver failures. The original fleet lifecycle owns collection
and resource termination. Historical staged validation remains available in its
original commits and frozen sources, not as a selector in the maintained runtime.
