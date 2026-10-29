# Working on the Python repair harness

The README introduces the project. `src/teamharness/DESIGN.md` owns investigation,
delegation, execution, and completion boundaries. `src/teamharness/CONTEXT.md` owns
the information supplied to the model. The runtime README owns local operation;
the adapter README owns packaging and external course execution.

Ground claims about behavior in the implementation or an actual serialized
request. The model chooses its investigation; the runtime records execution facts.
Keep reports, plans, and guidance distinct from observed process and file effects.

Investigation ownership belongs in `main.py`; conversation and context handling
in the team runtime; external course effects in the adapter; HTTP in `src/api.py`;
SQLite in `src/store.py`; and process/file effects in `src/workspace.py`. Avoid
duplicating these responsibilities in a new control layer.

Use focused verification for the affected behavior. Follow the
[verification guide](test/teamharness/README.md) for a reproduction or named test.
Do not run broad test discovery, repeated baseline suites, mutation campaigns,
or extended generated tests as routine review. Documentation-only changes need
diff and link checks. Do not install test dependencies merely to accompany a
reversible change, or launch paid evaluations without an explicit request.

Update the guide that owns a changed contract. Keep private traces, credentials,
resource inventories, and live campaign state outside this checkout. Preserve
unrelated source changes and active work. A passing local check establishes only
the behavior and environment that it actually exercised.
