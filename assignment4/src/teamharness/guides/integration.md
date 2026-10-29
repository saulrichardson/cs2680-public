# Integration and evidence

Use this when combining agents' patches, interpreting verification results, or
reviewing a claim that a repair is complete.

Know the source snapshot used by each child. A private patch is an artifact to
inspect and apply; it does not silently change the parent workspace. Patches that
are individually valid can conflict or depend on incompatible assumptions. After
integration, run the relevant checks against the resulting files. Earlier results
remain evidence about earlier candidates, not automatically about this one.

Private source copies share external state unless explicitly isolated. Dependency
caches, absolute symlinks, services and environment settings can outlive a role.
Inspect original receipts before claiming a symbol was present in the baseline.
If a repair changes external files, explain which bytes are outside the captured
patch and make required changes portable through source or declared dependencies.
Use a fresh replay/scratch environment with controlled dependencies when it can
resolve that uncertainty; a new repository copy alone does not reset the cache.
Check the actual installed version and fetch capability before relying on a
download. A setup failure is still zero executed behavioral tests.

Preserve existing tests and fixtures when adding a focused check. Check test
discovery conventions and actually collect/run it: a test-like filename or an
assertion that a callback exists does not establish behavior. Before changing an
expected fixture, derive the expected result from the original contract rather
than copying the candidate's output. When a helper gains a nullable/error return,
inspect its real callers and exercise that branch, not only the helper in isolation.

Follow the production path. A new helper or test-only mock can work while the
application still calls the old implementation. Read the real entry point and
exercise it. Keep mocks at controlled external boundaries where possible, rather
than using a replacement implementation that bypasses the behavior under test.

Read execution results literally. "Started" is not "finished". A failing import,
compiler, test configuration, or collection step means the intended behavior was
not checked. A successful shell pipeline can hide a failed earlier command; inspect
the command and actual test summary, or use a shell configuration that preserves
the relevant exit status. Expected no-match searches and failing baseline checks
are not necessarily repair failures. An unchanged process status does not diagnose
whether a background job is stuck.

Reviewer's task: look for an observable requirement that could remain false while
the present tests pass. Investigate the strongest concrete candidate. Report the
requirement, code location, reproduction or evidence, and its practical consequence.
Keep a supported hypothesis distinct from a reproduced cause. Do not manufacture
a finding. A reviewer can finish with no actionable defect while still describing
coverage limitations.

The test author can be mistaken too. Resolve disagreement against the original
task and observed behavior, not by counting votes or treating a role as authority.
If an assertion encodes an unsupported design choice, explain and correct it. If
an assertion exposes a real requirement, fix the implementation rather than
weakening the test.

Local checks, reviewer conclusions, and external grades have different authors.
Keep them distinct. The final report should state what changed, which checks ran
on the integrated candidate, what they observed, and what remains unresolved.
Do not use a checklist of assertions about source as a substitute for execution.

## Focused first deliverable and reuse

Begin independent tests with a small contract/real-consumer check that rejects
an important plausible wrong implementation. Reuse installed fixtures. Return
that useful artifact and its baseline evidence before growing an exhaustive suite;
expand when a distinct requirement or observed defect warrants it. Missing required
symbols can be an expected baseline contract failure, not permission to change the
required name. Fixture defects and behavioral failures need different corrections.

During integration, use the supplied captured patch identities and archived command
receipts to establish what each author actually did. Prior environment observations
can spare repeated setup investigation. Rerun relevant checks on the combined
candidate, since earlier passing evidence does not cover changed bytes. Avoid
repeating broad searches without a concrete unresolved question.

A real probe invokes the library or production path whose behavior is uncertain.
For example, printing "the decoder preserves null" only prints a claim. Call the
decoder on a controlled input and inspect its actual value/type instead. Keep
literal output, inference and the resulting decision separate in your handoff.
A useful handoff points to the observation that changed or confirmed the repair,
its exact artifact/output reference, and remaining uncertainty. More commands,
more tests, or a longer report are not progress by themselves.
