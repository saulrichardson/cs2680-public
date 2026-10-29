# Repair references: index and mechanism rules

This file is the index of the installed repair manuals and a list of rules that recurred in graded failures of this harness. This is optional authored guidance, not runtime authority. The main chooses its workflow. The current task and the checkout define the contract. Examples below name generic mechanisms, never a task's answer.

<a id="skill-routing"></a>

## Index

| Mechanism or question | Reference (relative to this file) |
|---|---|
| Syntax/encoding, representations, imports/linkage, matching, ordered output | [Data and parsing](.agents/skills/data-and-parsing/SKILL.md) |
| Overlap, deferred effects, timers/cancellation, UI events, media, streaming | [Async, UI, and streams](.agents/skills/async-ui-and-streams/SKILL.md) |
| Cache outcomes/policies, storage, migrations, public operations, desired-state diffs | [State, storage, and reconciliation](.agents/skills/state-storage-and-reconciliation/SKILL.md) |
| Schemas/defaults, interface/wrapper changes, credentials/trust, providers and routing | [Configuration, interfaces, and identity](.agents/skills/configuration-interfaces-and-identity/SKILL.md) |
| System/package inventory, subprocess/native/Qt behavior, ownership and cleanup | [Platforms, processes, and resources](.agents/skills/platforms-processes-and-resources/SKILL.md) |

<a id="optional-memory"></a>

| Language question | Cookbook |
|---|---|
| Import scope, mocked bindings, duplicate JSON fields, partial-acquisition cleanup, exception lifetime | [Python](.repair-memory/cookbooks/python.md) |
| Missing versus null, error identity, cancelled context, typed nil, build variants, offline modules | [Go](.repair-memory/cookbooks/go.md) |
| Async effects, promise ownership, byte views, event timing | [TypeScript/JavaScript](.repair-memory/cookbooks/typescript.md) |
| Guarded QObject pointers, portable key text, process outcome capture, binary decoding | [Native/platform](.repair-memory/cookbooks/native.md) |

Open a chapter with `grep -n '^#' <manual>` and `sed -n 'A,Bp'`. Read a chapter only when its row names the task's mechanism; a chapter map is not the chapter. [Reference models](.repair-memory/reference_models.py) are optional executable illustrations.

## Mechanism checks

Transferable checks for recurring mechanisms. They were distilled from graded failures on this benchmark's tasks, so they state methods, never a task's answer; bind each one to the current task's evidence. The current main prompt explains source identities and execution receipts; these checks are optional techniques.

- **Configuration reaches its consumer.** Load a file that sets the value with the key spelling the task states and assert the consumer sees it; load an empty file and compare the result with any defaults constructor.
- **Lifecycle timing.** A value the task says is read at call or construction time must be read there. Change the setting in-process after import and assert the new value takes effect.
- **Serialization.** When output must match a named library or fixture, build the reference with that library and compare the full bytes; substitute only data the task says varies.
- **Decoded shape.** Input that must have a given shape can parse successfully with another shape. Check the type before indexing or calling methods on it.
- **Read-modify-write.** For any getter/setter pair, print `setter(getter(x))` for one input containing an escape or special character before choosing modes.
- **Structured strings.** Parse versions, releases, paths and identifiers with the repository's existing helper, and test with values taken from its fixtures.
- **Effects.** A function that builds a request, command or description performs no effect by itself. Find the executor that existing callers pass it to.
- **Policies.** A policy marker (no-store, bypass, permission) must hold at every write or effect site, not at one caller. Call each site directly under the marker.
- **Events and time.** Drive the exact event sequence the requirement names, with the test environment's own primitives (its fake timers, the repository's flush helper).
- **Shared inputs.** Do not mutate an array or object received from a getter, prop or model; copy it first.
- **Units.** At a unit boundary, print one real value from running code before converting.
- **Concurrency and variants.** After adding goroutines or shared buffers, run the package's tests with `-race`. With build constraints, compile each variant.

<a id="method-verification"></a>

## Verification by mechanism

Select checks that distinguish a correct implementation from the plausible wrong fix. Use the repository's toolchain and fixtures; a self-written check counts only when it calls the public entry point with the same mock seam the repository's tests use and compares a value computed by the code against an expectation written before the run.

| Mechanism | Observe directly | Plausible wrong fix to catch |
|---|---|---|
| Cache policy | Backend counts, stored outcome, eligibility, invalidation | Correct first result but poisoned future calls |
| Async state | Intermediate transitions, effect counts, selected event order | Correct final display with duplicate or lost effects |
| Timers | Before/after boundary and callback ownership | Old timer changes a new operation |
| Value propagation | Exact harmless marker at the final consumer, presence, identity | Direct call works while the wrapper drops the argument |
| Parsing | Complete normalized output and required errors | Accepts a valid substring or erases payload |
| Linked records | Association, cardinality, order, missing partners | All values present but paired incorrectly |
| Configuration | Public input reaches the selected runtime dependency | Schema accepts an option that is unused |
| Persistence | Public response, read-back, actual query, old and new records | New field exists but query or backfill path is stale |
| Lifecycle | Surviving artifacts, owners, callbacks, visible state | Returned error is correct while resources leak |
| Reconciliation | Commands, changed status, resulting state, next diff | Change flag suppressed while effects remain |

<a id="method-independent-expectations"></a>

Prefer an existing fixture or the public contract over expected output copied from the new implementation. A round trip can be self-consistently wrong; a helper test can bypass a broken call path; a mocked helper shaped to the implementation excludes no wrong implementation. Where appropriate, check that a behavior test fails on the original code for the intended reason and passes after the edit.

<a id="method-report-evidence-accurately"></a>

Before finishing, compare every requirement with the final diff and the executed evidence. Report what ran, what passed or failed, and what remains unverified. A well-formed patch or a self-generated passing script is not evidence of the whole requested behavior.
