# Working in TypeScript and JavaScript repositories

A TypeScript declaration can compile while runtime data violates the assumption
behind it. A JavaScript test can execute while bypassing the application's module,
state or event path. A useful repair connects both: the types callers use and the
behavior the running program produces. Use the following examples to investigate
that connection, alongside the shared guidance on contracts and evidence.

## Establish the actual execution path

Read the owning package's scripts, runner configuration and nearby tests. In a
monorepo, the root, application and shared package can use different transforms,
aliases and environments. Prefer the installed project command. A generic `npx`
invocation can attempt to download a missing tool in an offline environment;
inspect the local binary and package scripts before relying on it.

When setup fails, identify its stage before choosing a repair: resolving a package,
fetching an archive, running an installation script, compiling a native addon,
transforming source or executing an assertion. Having a package directory does
not establish that its generated output or native binary is ready. A native build
may need runtime headers, a compatible toolchain and system libraries even when
the JavaScript dependencies are present. Follow the requested artifact and actual
runtime version; a header-download error is not a TypeScript error in your source.
Inspect prepared assets and supported project configuration rather than repeatedly
requesting the same unavailable network resource.

Preserve the project's package manager, lockfile and workspace relationships when
repairing installation. Before a clean reinstall, establish that the available
artifacts can reconstruct the environment: `npm ci`, for example, removes existing
`node_modules` before installation. Switching managers, deleting a lockfile or
ignoring lifecycle scripts changes the experiment and may discard the only working
local setup. Skipping a script is useful only if you understand what it supplies
and whether the check needs it. A missing generated client or native addon remains
missing even if the installer now exits successfully.

Check which artifact the caller loads after a source edit. A workspace dependency
may resolve to built output while a test alias resolves directly to source. Inspect
the relevant exports, scripts and resolved path under that caller's environment,
then run the necessary project build if the changed source must produce that output.
An ad hoc edit inside `node_modules` can clarify a dependency hypothesis, but it
does not repair the submitted repository. Use maintained source, dependency records
or an existing project patch mechanism when a dependency change is actually needed,
and return to the original failing command to check the consequence.

Determine whether the runner type-checks, transpiles only, or runs built output.
A successful Jest or Vitest test need not establish that `tsc` accepts the source.
Likewise, `tsc --noEmit` need not execute any behavior. Use the applicable project
configuration rather than compiling one file with unrelated defaults. For example,
where the repository uses these installed tools:

```sh
./node_modules/.bin/tsc --noEmit -p packages/widget/tsconfig.json
./node_modules/.bin/jest --runInBand --runTestsByPath packages/widget/example.test.ts
```

Adapt paths and flags to the project. Keep full output, process completion and
selected cases. Searching a saved log is safe; piping the running compiler into
`head` can lose diagnostics and change the process outcome. A filter's exit code
is not the compiler's. If the runner reports no tests, inspect its selection and
configuration before treating the operation as a check of your change.

Imports have runtime and type-level consequences. Check exports, barrel files,
path aliases, package exports and actual module resolution. A type-only import
cannot supply a runtime value. A cast changes TypeScript's view of a value, not
its actual fields. A successful isolated import can still miss an application
initialization cycle or a different entry point. Follow the caller whose loading
behavior matters instead of adding broad fallbacks for an unexplained error.

Search results locate candidate source. Include untracked additions and relevant
dependencies when necessary. Methods can be inherited, generated or assigned
dynamically, so no matching method declaration alone is not proof of absence.
Inspect the concrete producer and callable before changing production use.

## Keep data meaning across boundaries

Obtain field spelling, nesting and optionality from the public interface and real
producer. An API response, normalized store record and UI model may represent the
same entity differently. A plain object invented for a probe can conceal that
conversion. Exercise the actual normalization or use a fixture that represents
the relevant producer, while retaining the code under investigation.

Missing, `undefined`, `null`, false, zero and empty string can represent different
states. Consider why the program uses a fallback before changing its operator:

```js
false || true   // true: replaces every falsy value
false ?? true   // false: replaces only null or undefined
```

Neither operator is universally correct. A missing preference may mean inherit,
a false preference may mean explicitly disabled, and an absent value may need to
remain absent in serialization. Follow loading, computation, saving and reloading.
A locally sensible default can overwrite a user's choice at the next boundary.

When several inputs determine a result, choose a few examples that distinguish
their roles. For instance, a preference source, available credentials and an
explicit enable/disable choice may be independent dimensions. Hold two fixed and
vary the third to see whether the expected boundary changes. Then exercise one
complete load/action/save path. This is a way to expose precedence mistakes, not
a requirement to enumerate every combination before editing.

A new record kind or enum member may participate in several maps, switches,
serializers and UI options. Search existing siblings to discover those roles,
then decide which the public change affects. Adding a member everywhere without
understanding the role can expose unsupported behavior. Renaming a field also
requires following writers and readers; optional fields can make a missed
consumer quietly use a default instead of throwing.

Equality and identity answer different questions. Object spread copies one level;
nested records can remain shared. `Set` membership and `Map` object keys use
identity, while serialization may discard undefined properties or change values
that the program distinguished. A round-trip through your own encoder and decoder
can conceal the same mistake on both sides. Inspect the representation at the
external consumer when compatibility depends on it.

## Understand asynchronous work and failure state

Trace when work starts, resolves, rejects, updates state and becomes visible to
its caller. A function returning a promise and a caller awaiting it are separate
parts of that lifecycle. An unreturned promise can make a test finish before the
assertion, and a caught rejection can change the public result. Use the project's
async testing conventions and assert on the awaited operation.

A cache introduces identity, lifetime and invalidation questions. Determine what
identifies the operation and whether pending work, a successful result and a
rejection are retained under the same policy. A first request's rejection may
need to be reused; a transient error may need a new request; expiration may permit
retry. Drive those sequences through the real entry point and observe both the
returned result and the underlying request count. A helper that recognizes an
error code does not by itself verify cache behavior.

Error shape comes from the producing API and supported callers. `instanceof`
tests constructor identity; a structural check tests properties. Cross-realm
objects, deserialized errors or intentional substitutions can distinguish them,
but the possibility of an imagined hidden mock is not itself a reason to accept
arbitrary objects. Inspect the producer and contract, then choose an example that
settles the relevant distinction. Preserve specific exceptions or fields rather
than turning every rejected value into a generic success or empty result.

Timers and clocks need the same care. Read the installed runner's timer setup and
the clock the implementation uses. Advancing fake timers may not flush every
promise or scheduler queue. Await the observable completion rather than assuming
that an elapsed delay proves it happened. Boundary checks just before and after
expiration can clarify inclusive comparisons. Derive the interval from the task
or established code; do not guess it from how long a hidden test might wait.

Cancellation may only stop waiting while underlying work continues. Inspect the
actual abort path and any late result that can overwrite newer state. A
controllable promise or dependency can let a test deliver responses in an order
that exposes the problem. Keep production scheduling logic under test rather
than replacing it with a synchronous stand-in that removes the race.

## Use mocks and fixtures to test the disputed boundary

Read how the repository substitutes dependencies. A replacement may affect an
export before import, an instance method after construction or a provider supplied
through context. Hoisting, module caching and captured references can make those
operations behave differently. If the real dependency still ran, inspect the
actual lookup and import order before changing the production design.

Keep the part whose semantics matter real. A permissive mock accepting every
method cannot reveal an invented provider API. An overly narrow fake can also
reject an interface migration that the public task requires. Compare the fake,
real implementation and contract; use the result to decide which needs adaptation.
For stateful code, prefer the project's store and actions where practical so
initialization and selectors participate in the check.

An absent service, missing asset, invalid import and assertion mismatch need
different responses. Read the full diagnostic before adjusting source. Inspect
available local setup when a service is relevant; if the public case is supposed
to be served locally, trace why it attempted a network call. A workaround in this
sandbox does not establish the pristine grading environment has that workaround.

## Turn an explanation into a checked change

Use one stable test or probe for the consequential uncertainty. Preserve its
inputs and expected result while changing source. If a run stops during setup,
repair the setup and reach the same assertion instead of replacing the original
behavior with a simpler helper test. Label exploratory output honestly; a script
that prints `false` and exits zero has reported a discrepancy, not a pass.

For a move or refactor, compare the original behavior that should survive and
exercise the newly required path. A local test adaptation can be legitimate for
an intentional interface change, but updating a snapshot or mock to agree with
the implementation does not independently establish the contract. Read changed
snapshot fields and decide what each represents before accepting new output.

Keep exact specified names, literals, callback payloads and export locations.
For details the task leaves unspecified, inspect established interfaces and choose
an appropriate design; there is no reliable hidden-test naming rule to guess.
A new exported utility can be intended for external callers and need not gain an
invented internal caller. When the task changes an existing application operation,
trace its actual trigger so the new implementation is reached.

After coherent related edits, inspect the diff and run meaningful type and
behavior checks. Supported fixes can survive a change of theory. A repeated
aggregate grade does not show which part to revert; a specific contradictory
execution does give you something to investigate. Use that observation to narrow
the next action instead of reopening every file or adding speculative guards.
