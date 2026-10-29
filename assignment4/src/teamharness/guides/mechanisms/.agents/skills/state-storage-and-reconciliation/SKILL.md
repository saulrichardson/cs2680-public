---
name: state-storage-and-reconciliation
description: Repair cache policies, retained outcomes, request-scoped storage rules, persistent projections/migrations, public operations, and desired-state diffs. Use when lifetime, presence, side effects, old data, or convergence is wrong.
---

# Caching, persistence, and desired-state reconciliation

Determine which state exists, who can observe it, how long it is valid, and what operation is permitted to change it. Read bypass, write suppression, clearing, disabled initialization, pending sharing, and failure retention are independent. A successful write does not establish query/index/backfill consistency. User omission and device-output omission are different facts.

Trace the public path and storage side effects together. Use cache chapters for outcome/policy lifetimes; persistence for write/read/query/migration and API behavior; reconciliation for semantic diffs and command generation. The technical chapters supply disk-state, serializer, date-partition, optional-data, prerequisite, and batch semantics. Keep the required transaction/completion boundary explicit rather than assuming every successful helper completes the operation.

## Focused boundary checks

- [Check shared persistence paths and policy at the boundary](#persistence-paths)
- [Classify real errors and preserve cache scope](#cache-error-and-callers)
- [Match batch lookup keys and forwarding contracts](#batch-identity)


## Chapter map

- [Selective failure caching](#selective-failure-caching)
- [Request policy propagation](#request-context-propagation)
- [Persistence, API boundaries, and migration consistency](#persistence-and-migrations)
- [Desired-state reconciliation and idempotence](#desired-state-reconciliation)
- [Cache policy, disk state, and serialization](#domain-cache-storage-and-policy)
- [Persistent projections, time ranges, optional data, and public operations](#domain-persistence-and-api)

<a id="selective-failure-caching"></a>

## Selective failure caching

<a id="selective-failure-caching-decide-whether-retention-is-the-mechanism"></a>

### Decide whether retention is the mechanism

Observe sequential calls and overlapping calls separately. Sequential repetition after a completed failure suggests outcome retention; simultaneous duplicate work suggests in-flight sharing. Immediate retries after a permission change may be correct. Do not turn every repeated request into a cache problem.

Trace the existing lookup, backend call, wrapper, cache key, and error return path. Discover the operation identity and scope, error classification, required error shape/identity, lifetime, invalidation events, and successful-result policy. Treat each as an independent contract choice.

<a id="selective-failure-caching-select-the-policy"></a>

### Select the policy

| Observation | Investigate |
|---|---|
| Same eligible failure reaches backend repeatedly | Whether the settled failure is stored and read at the same boundary |
| Transient outage prevents future attempts | Overbroad error classification or retained pending rejection |
| Another user/resource receives the same failure | Missing scope in the identity or incorrectly shared storage |
| Failure survives an update that should make it obsolete | Invalidation, expiry, or a late completion repopulating old state |
| Only overlapping requests duplicate work | Pending-operation sharing rather than failure retention |

<a id="selective-failure-caching-implement-at-the-existing-boundary"></a>

### Implement at the existing boundary

Separate pending operations, stored successes, and retained failures even if the current implementation represents them in one container. Keep classification close to the boundary that knows the failure semantics. Do not retain cancellation, decode errors, or transport errors unless the contract makes them eligible.

Use the same identity on lookup, write, and invalidation. If invalidation can happen during a request, determine whether an epoch/version or conditional store is needed to stop an old completion from restoring obsolete state. A global boolean can incorrectly couple unrelated keys.

Release pending bookkeeping on every settlement. When an older request settles after a replacement started, remove only its own entry. Preserve caller-visible error behavior rather than converting all failures to an invented sentinel.

<a id="selective-failure-caching-verify-the-confirmed-policy"></a>

### Verify the confirmed policy

Count backend calls and observe returned outcomes for eligible and transient failures, another identity/scope, expiry, and invalidation. Add overlap tests only if sharing or concurrent invalidation is required. Use controlled time and settlement order rather than sleeps. If no failure may be retained, leave retention out and follow the confirmed cause instead.

<a id="selective-failure-caching-mechanism-details"></a>

<a id="selective-failure-caching-failure-policy-algorithms-and-worked-cases"></a>

### Failure policy, algorithms, and worked cases

These examples illustrate independent mechanisms. Derive actual key fields, errors, durations, and data structures from the current contract.

<a id="selective-failure-caching-details-three-separate-lifetimes"></a>

<a id="selective-failure-caching-three-separate-lifetimes"></a>

#### Three separate lifetimes

| Object | Lifetime ends when | Common mistake |
|---|---|---|
| Pending operation | The underlying work settles or its ownership changes | Leaving a rejected promise installed forever |
| Successful outcome | Its success-storage policy expires or invalidates it | Accidentally adding success caching while fixing error retention |
| Retained failure | Its error-specific retention policy expires or invalidates it | Treating every failure as permanent |

Joining a pending request need not imply storing its settled result. Retaining one error class need not imply retaining another.

<a id="selective-failure-caching-details-conditional-algorithm"></a>

<a id="selective-failure-caching-conditional-algorithm"></a>

#### Conditional algorithm

```text
lookup(identity):
    if a permitted retained outcome is still valid:
        return/rethrow that outcome according to the public interface
    if sharing is required and an owned pending operation exists:
        join it
    remember the relevant invalidation epoch, if concurrent invalidation is possible
    start the underlying operation
    on success:
        store only if success storage is required and this completion is still current
    on failure:
        store only if eligible and this completion is still current
        propagate the required failure
    finally:
        remove pending bookkeeping only if it still belongs to this operation
```

An epoch is one possible implementation, not a requirement to add new state everywhere. Another valid design may cancel old work or perform a conditional replacement in an existing store. Follow the repository's concurrency model.

<a id="selective-failure-caching-details-worked-case-a-missing-document-until-publication"></a>

<a id="selective-failure-caching-worked-case-a-missing-document-until-publication"></a>

#### Worked case A: missing document until publication

A document service permits retaining a not-found outcome until a publication event. The identity includes collection and document ID. The first miss calls the backend; a second same-identity request reproduces the permitted failure without another backend call. A request in another collection is independent. Publication invalidates the retained miss, and the next request reaches the backend and succeeds.

A late pre-publication request must not overwrite the new state with its old miss. Force that completion after publication in a controlled test if this ordering is possible.

<a id="selective-failure-caching-details-worked-case-b-invalid-catalogue-item-with-expiry"></a>

<a id="selective-failure-caching-worked-case-b-invalid-catalogue-item-with-expiry"></a>

#### Worked case B: invalid catalogue item with expiry

A catalogue client retains an invalid-item result for its specified lifetime while gateway timeouts remain retryable. At controlled times before and after expiry, inspect both the returned result and backend count. Discover whether the boundary instant is inclusive from existing conventions; do not invent that detail. A timeout must allow a later attempt to reach the backend.

<a id="selective-failure-caching-details-counterexample-access-denial"></a>

<a id="selective-failure-caching-counterexample-access-denial"></a>

#### Counterexample: access denial

A user receives access after an initial denial. Retaining that denial across the permission change suppresses a valid request. Use permission-aware invalidation or avoid retaining this class, according to the public policy. “Errors are expensive” is insufficient grounds for blanket negative caching.

<a id="selective-failure-caching-details-acceptance-matrix"></a>

<a id="selective-failure-caching-acceptance-matrix"></a>

#### Acceptance matrix

For each eligible class, test repeat identity, distinct identity, scope, expiry, invalidation, and required error representation. For ineligible classes, test that another attempt is possible. If operations overlap, also test old completion after replacement and failure after invalidation. Check final store contents as well as visible outcomes; plausible returned values can conceal poisoned future state.

<a id="selective-failure-caching-cache-policy-boundaries"></a>

### Cache policy boundaries

For disk initialization, version/permission handling, credential-free identities, multiple serializers, and no-store propagation, read [cache storage and policy](#domain-cache-storage-and-policy). Do not collapse those independent options into the retention rule.

<a id="request-context-propagation"></a>

## Request policy propagation

<a id="request-context-propagation-define-the-policy-precisely"></a>

### Define the policy precisely

List what the option changes at each layer. Cache read bypass, write suppression, invalidation, and disabling storage are different behaviors. Determine whether the policy is per request, per client, or global. Do not implement a global switch to simulate request-scoped behavior.

Trace producer → middleware → wrapper → consumer → side effect. At every edge record the carrier, default, transformation, and whether a new context/request is created. A marker visible at the entry point does not establish that the backend observes it.

<a id="request-context-propagation-find-the-broken-edge"></a>

### Find the broken edge

Substitute or spy on the final consumer. Compare omitted, explicit normal, and explicit special policy through the real entry point. Inspect both reads and writes. Determine whether the value disappears during copying, is shadowed by a default, is checked at the wrong layer, or is interpreted inconsistently by multiple backends.

<a id="request-context-propagation-repair-transport-and-consumption"></a>

### Repair transport and consumption

Preserve request scope through constructors, decorators, retry callbacks, and batch adapters. Reuse the existing typed carrier or option mechanism. Reconstructing a bare request or background context can discard more than the new marker, including cancellation and identity.

Apply an option where its side effect is actually selected. Checking “do not write” after opening or creating a file may violate a no-storage-side-effects contract. Conversely, bypassing a read need not suppress a required write. Separate these decisions using the documented semantics.

Where policies compose, derive precedence from the public contract or existing architecture. Record a small truth table; do not choose “strictest wins” universally when callers can deliberately override defaults. Keep unrelated requests isolated.

<a id="request-context-propagation-verify-end-to-end"></a>

### Verify end to end

Use call counts and observed storage effects for omitted/default/special policy, nested wrappers, another request, and each supported backend. Test cancellation or retries only if the transport path includes them. A helper test that checks the marker exists does not establish that the feature works. If only one optional argument is lost and no request policy is involved, the value-propagation chapter may be sufficient.

<a id="request-context-propagation-mechanism-details"></a>

<a id="request-context-propagation-policy-transport-composition-and-side-effects"></a>

### Policy transport, composition, and side effects

<a id="request-context-propagation-details-policy-ledger"></a>

<a id="request-context-propagation-policy-ledger"></a>

#### Policy ledger

For each hop record `incoming carrier → transformation → outgoing carrier → consuming behavior`. Test the consumer with a spy that records the effective policy, not merely the original argument passed to the public function.

| Policy concept | Question to answer |
|---|---|
| Bypass reads | Must the backend be reached even if a cached result exists? |
| Suppress writes | May the result update persistent or in-memory cache state? |
| Invalidate | Which entries become unusable, and when? |
| Disable storage | Are initialization, directory creation, permissions, or warnings also forbidden? |
| Cancellation/deadline | Does derived work retain the request's stopping conditions? |

These are dimensions to discover, not a demand to add five new options.

<a id="request-context-propagation-details-worked-case-a-refresh-policy-through-decorators"></a>

<a id="request-context-propagation-worked-case-a-refresh-policy-through-decorators"></a>

#### Worked case A: refresh policy through decorators

A data client has an authorization decorator, a cache decorator, and a backend adapter. The public request says to refresh from the backend while still updating the cache. If the authorization decorator reconstructs a request with only the URL, the policy can disappear. If the cache interprets refresh as “skip both read and write,” the transport is intact but consumption is wrong.

Use a backend counter and a cache-write counter. Seed a cached result, make the refresh request, and inspect both counters and the returned value. A marker assertion alone cannot distinguish these failures.

<a id="request-context-propagation-details-worked-case-b-optional-disk-storage"></a>

<a id="request-context-propagation-worked-case-b-optional-disk-storage"></a>

#### Worked case B: optional disk storage

A client can operate with disk storage disabled. Determine whether this means no cache accesses, no writes, or no filesystem initialization at all. If the contract forbids storage side effects, constructing the cache directory before checking the option is already a defect even when later reads/writes are bypassed. Verify file and directory absence and any specified warning behavior.

Keep this distinct from a cache miss: a miss may legitimately create storage under the enabled policy.

<a id="request-context-propagation-details-counterexample-per-request-policy-stored-on-a-shared-client"></a>

<a id="request-context-propagation-counterexample-per-request-policy-stored-on-a-shared-client"></a>

#### Counterexample: per-request policy stored on a shared client

A special request flips a shared client's boolean and resets it later. An overlapping normal request can observe the special policy. Transport request-scoped state with the request or use an existing scoped mechanism. Resetting a global flag in `finally` does not remove the overlap bug.

<a id="request-context-propagation-details-composition-procedure"></a>

<a id="request-context-propagation-composition-procedure"></a>

#### Composition procedure

List all policy sources: default, client configuration, caller option, middleware-derived rule. Write expected effective policy for the combinations the API permits. Identify the layer authorized to resolve conflicts. Preserve absence until that layer if absence means “inherit.” Once resolved, downstream code should consume a consistent policy rather than independently re-defaulting it.

<a id="request-context-propagation-details-checks"></a>

<a id="request-context-propagation-checks"></a>

#### Checks

Cover the real wrapper chain, omitted option, explicit normal policy, special policy, a second unaffected request, and every implementation that consumes the carrier. If retries clone requests, observe the retry path as well. Check a failure path where cleanup or warning code could perform the forbidden side effect. Keep diagnostics scoped so credentials or sensitive raw request data are not needed in test output.

<a id="request-context-propagation-serialization-and-storage-consumers"></a>

### Serialization and storage consumers

Read [cache storage and policy](#domain-cache-storage-and-policy) when the marker controls cache serializers or disk state. Read [service wiring](../configuration-interfaces-and-identity/SKILL.md#domain-service-wiring) when it controls protocol propagation or runtime dependency construction. A marker assertion alone cannot establish the downstream behavior.

<a id="persistence-and-migrations"></a>

## Persistence, API boundaries, and migration consistency

<a id="persistence-and-migrations-trace-the-complete-operation"></a>

### Trace the complete operation

Start at the route, command, or public API. Follow decoding, validation, actor/target authorization, mutation, storage, query selection, and response shaping. For schema/index changes, add old records, new writes, backfill, and migration registration/replay to the map.

Define missing/null/zero/populated values separately. Determine whether a value is transient request input, persisted state, computed output, or an indexed projection. Do not persist a request-only credential or selector merely because it appears in the input model.

<a id="persistence-and-migrations-diagnose-the-inconsistency"></a>

### Diagnose the inconsistency

Write a requirement-to-path ledger. Read back an actual write through the consumer that uses it. If old records fail while new ones work, inspect decoding/default/backfill assumptions. If reads work but queries miss records, inspect projection/index selection rather than only the model definition.

<a id="persistence-and-migrations-implement-all-required-edges"></a>

### Implement all required edges

Use existing transaction and migration mechanisms. Decide which writes must be atomic and which failures must leave no partial visible change, based on the contract. Preserve established error mapping and authorization order so a helper-only change does not bypass public checks.

For a new field/index, inspect write paths, readers, queries, serialization, old-record compatibility, and migration execution. Registering a migration without invoking it, or backfilling without updating new writes, leaves one population wrong. Make replay behavior explicit; use checkpoints or idempotence where the repository requires them.

If an actor acts on a different target, build an actor/target/action matrix from public policy. Keep checks at the correct public boundary and use the required identity. Do not generalize one branch's privilege into a universal bypass.

<a id="persistence-and-migrations-verify-visible-behavior-and-stored-results"></a>

### Verify visible behavior and stored results

Test required successful and failure responses through the public entry point. Read back state, query using the real selection path, and inspect response shape. Include old/new records and repeated migration only when relevant. Check failure between coupled writes when atomicity is promised. A model field, helper test, or index declaration alone is not completion.

<a id="persistence-and-migrations-mechanism-details"></a>

<a id="persistence-and-migrations-persistent-data-and-public-api-cases"></a>

### Persistent data and public API cases

<a id="persistence-and-migrations-details-consistency-ledger"></a>

<a id="persistence-and-migrations-consistency-ledger"></a>

#### Consistency ledger

| Concern | Questions |
|---|---|
| Public input | Which fields are accepted, required, and request-only? |
| Authorization | Who acts, on which target, for which action? |
| Mutation | Which values change, and which operations must be atomic? |
| Stored representation | How do old, missing, zero, and new values decode? |
| Query/index | Does the actual query use the new projection or key? |
| Response | Are public fields/errors shaped correctly? |
| Migration | Is it registered, executed, resumable/replayable as required? |

<a id="persistence-and-migrations-details-worked-case-a-normalized-search-field"></a>

<a id="persistence-and-migrations-worked-case-a-normalized-search-field"></a>

#### Worked case A: normalized search field

A directory adds a normalized lookup field. Updating the model and new writes can leave old records absent from queries. Backfilling old rows alone can leave later writes missing the projection. Trace both populations through the actual query, preserving the source value if the display contract requires it.

Use an old record without the field, a new record, a changed record, and a value whose normalization distinction matters. Run the migration under the repository's mechanism. If replay is supported, run it again and assert no unintended changes or duplicate side effects. A database index does not populate the field it indexes.

<a id="persistence-and-migrations-details-worked-case-b-actor-dependent-update"></a>

<a id="persistence-and-migrations-worked-case-b-actor-dependent-update"></a>

#### Worked case B: actor-dependent update

An API permits different operations depending on whether the authenticated actor is the target or has a specified administrative role. Build explicit rows for each allowed/denied actor-target-action combination. Trace the request through the public route so validation and authorization are exercised. A persistence helper called with prevalidated inputs cannot establish that the route enforces the policy.

Separate request-only verification input from stored update fields. On a rejected update, assert both the required response and unchanged persistent state. Preserve error behavior for missing targets and malformed input according to the contract.

<a id="persistence-and-migrations-details-counterexample-unnecessary-migration"></a>

<a id="persistence-and-migrations-counterexample-unnecessary-migration"></a>

#### Counterexample: unnecessary migration

A field is computed on read and the contract requires no persistent projection. Adding a stored column and backfill creates synchronization work without solving the actual behavior. Classify the value's role before choosing a migration.

<a id="persistence-and-migrations-details-atomicity-and-replay"></a>

<a id="persistence-and-migrations-atomicity-and-replay"></a>

#### Atomicity and replay

Identify the commit boundary. If two writes must become visible together, use the existing transaction/rollback mechanism rather than hoping failures will not occur between them. If a migration emits external effects, repeating it may need a checkpoint or deduplication key under the established design. “Upsert every row” is not universally idempotent when triggers or side effects exist.

<a id="persistence-and-migrations-details-checks"></a>

<a id="persistence-and-migrations-checks"></a>

#### Checks

Observe public result, stored value, query result, and response serialization for the relevant paths. Test old/new/missing values and a failure that could leave partial state. Verify migration registration and the actual selection of data to migrate. Preserve the original operation error when cleanup also fails, using the repository's error convention.

<a id="persistence-and-migrations-projection-optional-data-and-route-contracts"></a>

### Projection, optional-data, and route contracts

Read [persistence and API contracts](#domain-persistence-and-api) for pagination, date partitions, old optional data, prerequisites, ordered projections, and inspectable batches. Read [identity and credentials](../configuration-interfaces-and-identity/SKILL.md#domain-identity-and-credentials) for actor/target checks and signed resource identity.

<a id="desired-state-reconciliation"></a>

## Desired-state reconciliation and idempotence

<a id="desired-state-reconciliation-define-the-state-model"></a>

### Define the state model

Separate user intent, observed state, and platform defaults. Determine whether an omitted field means inherit, leave unchanged, reset, or use a mode-specific default. Explicit false/empty is not automatically omission. Preserve fully qualified identifiers when short names can collide.

List supported modes and their treatment of unspecified fields and resources. Read the repository's actual merge/replace/delete/check-mode contracts. Do not infer semantics solely from a mode's name.

<a id="desired-state-reconciliation-separate-normalization-diff-and-execution"></a>

### Separate normalization, diff, and execution

Normalize equivalent representations without erasing presence or identity. Compute the required change as data before issuing effects. Inspect the exact generated command sequence, including absence of commands for an already-correct resource.

<a id="desired-state-reconciliation-repair-the-comparison-rather-than-masking-effects"></a>

### Repair the comparison rather than masking effects

If repeated runs keep reporting changes, find the difference that never converges: wrong default, asymmetric normalization, incomplete device parsing, reordered collections, or an unconditional command. Suppressing a changed flag while still emitting effects hides the defect.

Use mode-specific desired-state construction and the existing platform adapter. Distinguish deleting a resource from resetting one property. Preserve command ordering where dependencies require it. If the platform's reported state omits defaults, compare effective state using the supported default model rather than treating every absent field as false.

<a id="desired-state-reconciliation-verify-convergence-and-exact-effects"></a>

### Verify convergence and exact effects

Test absent, existing-correct, and existing-different resources; omitted and explicit values; applicable modes; and check mode. Assert emitted commands, reported change, and resulting state where a simulator or fixture is available. Reconcile again against the resulting observed state and expect no further effects when idempotence is promised. Do not claim convergence by running twice against the same unchanged fake current state.

If the requested operation is explicitly imperative and must run every time, do not force desired-state semantics onto it. Use idempotence only where the public contract requires it.

<a id="desired-state-reconciliation-mechanism-details"></a>

<a id="desired-state-reconciliation-defaults-modes-and-reconciliation-examples"></a>

### Defaults, modes, and reconciliation examples

<a id="desired-state-reconciliation-details-keep-presence-until-intent-is-resolved"></a>

<a id="desired-state-reconciliation-keep-presence-until-intent-is-resolved"></a>

#### Keep presence until intent is resolved

```text
intent = parse user input while preserving omitted fields
current = parse observed platform state
desired = apply the documented mode and defaults to intent/current
changes = compare semantically equivalent desired/current values
commands = render changes using the platform's required order
execute only outside check mode
```

Some systems compute changes against a partial desired state rather than constructing a full state. Preserve the existing model if it expresses the contract clearly.

<a id="desired-state-reconciliation-details-decision-dimensions"></a>

<a id="desired-state-reconciliation-decision-dimensions"></a>

#### Decision dimensions

| Dimension | Distinctions |
|---|---|
| Presence | Omitted, explicit enabled/disabled, explicit empty/reset |
| Current state | Absent, already correct, different, omitted default |
| Mode | Only modes actually supported and their documented semantics |
| Identity | Full namespace/path, resource name, property |
| Execution | Normal, check/dry-run, repeated application |

<a id="desired-state-reconciliation-details-worked-case-a-feature-enabled-by-the-device-default"></a>

<a id="desired-state-reconciliation-worked-case-a-feature-enabled-by-the-device-default"></a>

#### Worked case A: feature enabled by the device default

A device omits a feature flag when it has its default enabled state. The caller explicitly requests enabled. Treating absent device output as disabled produces a redundant enable command every run. Resolve the observed omission using the actual platform/version default, then compare effective values. An explicit request to disable still needs the appropriate command.

If the caller omits the flag, determine the mode's policy: it may leave current state untouched rather than reasserting the platform default. User omission and device-output omission are different facts.

<a id="desired-state-reconciliation-details-worked-case-b-merge-versus-replace-of-a-named-resource"></a>

<a id="desired-state-reconciliation-worked-case-b-merge-versus-replace-of-a-named-resource"></a>

#### Worked case B: merge versus replace of a named resource

A resource has a label and a description. A merge request supplies only a new label; under an inherit-unspecified contract, preserve the description. A replace request may clear it if that mode explicitly resets unspecified properties. Construct expected state separately for each mode, then render the diff.

Use two fully qualified resource identities sharing a short name to expose accidental cross-resource comparison. Compare ordered versus unordered collections according to the domain instead of sorting every list automatically.

<a id="desired-state-reconciliation-details-counterexample-imperative-action"></a>

<a id="desired-state-reconciliation-counterexample-imperative-action"></a>

#### Counterexample: imperative action

A command requests a one-time diagnostic capture on every invocation. Returning no effects because a previous capture exists violates its semantics. Idempotence is a property of the operation's contract, not a universal rule to impose on all commands.

<a id="desired-state-reconciliation-details-convergence-check"></a>

<a id="desired-state-reconciliation-convergence-check"></a>

#### Convergence check

Given current state C, compute changes D and the resulting state C'. Reconcile the same intent against C'. Under an idempotent contract, the second diff is empty. A check-mode run should report the predicted D without mutating state. Test the adapter's parsing of C' too: a correct abstract diff can still fail to converge when emitted command state and observed device representation are normalized differently.

<a id="desired-state-reconciliation-details-exact-effect-assertions"></a>

<a id="desired-state-reconciliation-exact-effect-assertions"></a>

#### Exact effect assertions

Check command contents and order, absence of unnecessary commands, changed/no-change status, and required errors. Do not repair only the reported status. If effects fail partway through, follow the module's established failure/rollback policy and preserve enough evidence to diagnose the remaining state.

<a id="desired-state-reconciliation-details-explicit-modes-and-command-ownership"></a>

<a id="desired-state-reconciliation-explicit-modes-and-command-ownership"></a>

#### Explicit modes and command ownership

A common family of modes merges specified fields, replaces fields within selected resources, overrides a managed resource set, or resets selected resources to defaults. Those are candidate semantics to verify, not universal definitions. Write separate desired-state construction for each supported mode and distinguish a new resource from an existing default-only one.

Keep device-wide defaults, resource-type defaults, current explicit settings, and user intent as separate inputs. Changing a mode can change the effective baseline of another property; command order then matters. Render dependency-changing commands before dependent settings when required. Collect reset/set operations for one resource before emitting its header so the header appears only as often as the protocol permits.

Test the pure diff independently from transport, then test its public executor once. A no-change result needs both empty effects and the correct changed flag. Check mode reports the predicted diff without applying it. Apply a modeled change to obtain the next observed state before testing convergence; reusing an unchanged fake device response does not establish a second-run no-op.

<a id="desired-state-reconciliation-observation-before-comparison"></a>

### Observation before comparison

Verify the fact collector and config manager use their actual connection contracts. A mapping of command outputs, an executor, and an absent connection are different interfaces. Preserve default-only resources as existing, even when they have no non-default attributes, and avoid leaking internal fact metadata into public argument-shaped facts. Derive mode/platform default tables from the permitted contract instead of importing a default from another device family.

<a id="domain-cache-storage-and-policy"></a>

<a id="domain-cache-policy-disk-state-and-serialization"></a>

### Cache policy, disk state, and serialization

This is a repair model for application caches, not a universal HTTP cache implementation. Learn each option's actual semantics from the public API. The examples are invented; they do not prescribe a target store name, version, permissions, or error list.

<a id="domain-cache-storage-and-policy-independent-policy-dimensions"></a>

<a id="domain-independent-policy-dimensions"></a>

#### Independent policy dimensions

| Dimension | Decision controlled |
|---|---|
| Read existing state | Whether an eligible stored result can answer the request |
| Write a new result | Whether a completion may update the store |
| Clear state | Whether old state is removed, and whether this precedes initialization |
| Initialize persistent storage | Whether construction may create directories/files |
| Retain a failure | Which settled error classes remain reusable |
| Share pending work | Whether overlapping callers join one operation |

Build a truth table only for options the API exposes. “Do not cache” can mean several combinations; its name alone is insufficient. A clear operation can be an explicit action independent of whether subsequent reads/writes are enabled. A constructor that opens a file before checking a disabled flag has already performed a side effect.

<a id="domain-cache-storage-and-policy-disk-initialization-is-a-state-machine"></a>

<a id="domain-disk-initialization-is-a-state-machine"></a>

#### Disk initialization is a state machine

Separate absent directory, absent file, valid supported data, incompatible data, malformed bytes, unacceptable permissions, and an I/O failure. Define the transition for each. A valid file may have to remain byte-for-byte and permission-for-permission unchanged; initialization is not permission to rewrite it. A dangerous or malformed file may need ignore, warning, reset, or error under different contracts.

Read configuration at the specified lifecycle point. Import-time constants and captured default arguments can retain an old directory even when the runtime configuration has changed. The correct fix may be moving the lookup to initialization rather than adding another config option.

Treat serialization as an interface when exact bytes are required. Whitespace, key ordering, newline, version representation, and the encoding used by a consumer can matter. Do not change formatting as incidental cleanup. If writing atomically is needed, use the existing store's safe replacement mechanism and verify failure before publication leaves the previous valid state usable. Do not invent a transaction scheme where the contract requires a simpler observable operation.

For warnings, enumerate which branch emits one and who owns it. If both the validator and caller warn, a single bad file can produce duplicates. Test count and call shape where specified, and do not send unrelated diagnostics through an API reserved for one warning condition.

<a id="domain-cache-storage-and-policy-identity-and-secret-free-metadata"></a>

<a id="domain-identity-and-secret-free-metadata"></a>

#### Identity and secret-free metadata

Parse service URLs structurally. Embedded user information, hostname, port, path, and query are separate fields. An identity based on the raw authority string can accidentally include credentials. Determine whether scheme, effective/default port, namespace, or user scope belongs in the key. Strip secrets without collapsing identities that the contract distinguishes. Verify an explicit port, missing port, malformed port, IPv6 if supported, and credential-bearing input using synthetic secrets.

<a id="domain-cache-storage-and-policy-multi-format-cache-values"></a>

<a id="domain-multi-format-cache-values"></a>

#### Multi-format cache values

Map each cached entity type to its serialization and key identity. JSON and protobuf are not interchangeable just because both occupy byte buffers. A cache hit must include successful decoding into the intended destination. Decide whether corrupt data is a miss or an error under the public policy. Never return success merely because bytes existed.

For writes, encode before publishing the value. If encoding fails, do not write a partial/default value. A request-level no-store marker must be checked by every relevant serializer's write path, including helper calls and error/fallback branches. Preserve the existing read behavior unless the policy also changes reads.

The marker must survive middleware, request metadata adaptation, derived contexts, storage decorators, and retry callbacks. A context key can collide with unrelated libraries when it uses a globally reused primitive identity; follow the language/framework's scoped-key convention. Do not replace a request context with a fresh background context and lose cancellation or identity.

<a id="domain-cache-storage-and-policy-failure-retention-with-invalidation"></a>

<a id="domain-failure-retention-with-invalidation"></a>

#### Failure retention with invalidation

Classify failures by semantics, not by whether an exception was thrown. A missing immutable object can be retained under one contract; a timeout, revoked/added permission, or incomplete decode may require another attempt. Preserve required error object identity when callers depend on it. Joining an in-flight promise is independent of retaining its rejection after settlement.

If a request starts under generation g and invalidation advances to g+1, admit its completion only under the contract's freshness rule. Removing an old pending entry should compare operation identity; otherwise an old completion can delete a newer request's bookkeeping. Scope generations by the actual identity if unrelated entities can progress independently.

<a id="domain-cache-storage-and-policy-worked-diagnostic-experiment"></a>

<a id="domain-worked-diagnostic-experiment"></a>

#### Worked diagnostic experiment

An invented client has a public request option, a disk-backed store, and a JSON/protobuf decorator. Instrument file creation, cache reads/writes, backend calls, and returned errors. Test disabled construction, explicit clear, enabled miss, valid existing state, malformed state, and disallowed permissions. Then carry a write-suppression marker through the public wrapper for both serializers. Finally, invalidate during an outstanding lookup and settle the old request after a new one. These observations distinguish policy transport, disk initialization, encoding, and lifetime bugs that a single “cache works” test conflates.

For the conditional lookup algorithm and retention examples, read [failure-cache policy](#selective-failure-caching-mechanism-details). For request-scoped composition, use [request policy](#request-context-propagation-mechanism-details).

<a id="domain-persistence-and-api"></a>

<a id="domain-persistent-projections-time-ranges-optional-data-and-public-operations"></a>

### Persistent projections, time ranges, optional data, and public operations

Use for changes whose correctness spans input, stored state, queries, and response shaping. Concrete table/index names, date formats, route names, and authorization decisions belong to the current issue, not this reusable guide.

<a id="domain-persistence-and-api-evolve-a-projection-as-one-system"></a>

<a id="domain-evolve-a-projection-as-one-system"></a>

#### Evolve a projection as one system

List producers, stored attributes, readers, query selection, indexes, migration registration, backfill execution, and old-record compatibility. A new indexed field needs both future writes and existing records handled. A new index that no query uses is disconnected; a query that uses it before backfill completes can miss old data.

Choose the completion contract explicitly: synchronous backfill before returning, asynchronous migration with readiness state, or another established process. Pagination belongs to the scan/query API, not an assumption that one call saw the whole dataset. Test more than one page when traversal is relevant. Replaying a migration can repeat external effects even when a row upsert is idempotent.

Determine which fields must always be materialized, which can be absent, and how types are encoded. A storage API that expects numeric attributes must not receive a string merely because the value looks numeric. Use the existing adapter and verify the actual bound query attributes and public results.

<a id="domain-persistence-and-api-partitioned-time-searches"></a>

<a id="domain-partitioned-time-searches"></a>

#### Partitioned time searches

Separate coarse partition selection from exact timestamp filtering. Normalize time in the storage contract's timezone before deriving a day/month partition. Enumerate every partition touched by the requested interval, including month/year boundaries. Apply exact lower/upper bounds under the required inclusive/exclusive semantics; fetching all selected days is not equivalent to filtering the interval.

```text
normalize the requested endpoints according to the API's timezone policy
enumerate the storage partitions intersecting that interval
query each partition with its actual key/range constraints
collect pages and enforce the required ordering/deduplication
return only records admitted by the exact endpoint policy
```

Do not hardcode a current date or use a display-timezone string as a key. Test a same-partition interval, a boundary instant, a cross-month interval, and a migrated old record. A fixed clock makes lifecycle/date checks reproducible.

<a id="domain-persistence-and-api-preserve-optional-scalars-across-upgrades"></a>

<a id="domain-preserve-optional-scalars-across-upgrades"></a>

#### Preserve optional scalars across upgrades

For timestamps or optional numbers, trace database null → model representation → helper assignment → update path → API serialization. A pointer helper that replaces nil with a pointer to zero can change the public response without any explicit migration. An omitted field and an epoch/zero timestamp are not automatically equivalent.

Build a table for old absent, explicit zero, populated, new absent, and updated values. Test read and write paths. Match JSON/database omission rules to the contract; equality of internal numeric values does not prove identical public representation. If nil versus empty collection is observable, preserve that distinction rather than normalizing it incidentally.

<a id="domain-persistence-and-api-public-authorization-and-prerequisites"></a>

<a id="domain-public-authorization-and-prerequisites"></a>

#### Public authorization and prerequisites

Map route parameter parsing, authenticated actor, target, prerequisite queries, mutation, and response. A persistence helper accepting prevalidated data cannot establish that the route enforces a prerequisite or rejects an unauthorized action. Check a required failure before mutating state, then assert both the error response and unchanged storage.

A read prerequisite may depend on work/edition/resource identity rather than the route's display ID. Trace actual IDs through lookups and ensure the requested record is the one checked. Do not invent permissive fallback when a required related record is missing. Response shape and operation return values remain part of the public contract.

<a id="domain-persistence-and-api-ordered-projections-and-inspectable-update-batches"></a>

<a id="domain-ordered-projections-and-inspectable-update-batches"></a>

#### Ordered projections and inspectable update batches

Separate gathering updates from executing them. A batch should preserve explicit adds, deletes, commit options, and execution results if consumers inspect those. Mutable default collections shared across instances can cross-contaminate independent batches. Test two batches created separately and change one.

For asynchronous per-record work, retain each result's record identity while aggregating into one execution command. Decide ordering from the API, not completion timing. If deletion and addition for the same identity can coexist, use the established reconciliation rule rather than implicitly choosing whichever callback finished last.

Catalog-driven ordered output needs an explicit join: selected IDs, catalog order, label lookup, unknown-ID behavior, and persisted representation. Sorting labels alphabetically can violate a catalog order even when every label is present. A full output assertion should include positions and missing values.

<a id="domain-persistence-and-api-independent-verification"></a>

<a id="domain-independent-verification"></a>

#### Independent verification

Use old/new records; at least two query pages if supported; a time range across a partition boundary; absent/zero/set values; a rejected public operation; two independent batches; and out-of-order completion. Choose relevant cases, not every cell. Observe stored values, actual query arguments, selected index, public response, and returned execution result. Passing only a helper or schema test establishes one edge of this system.

## Optional deeper cookbook

The mechanism and required technical facts are in this file. For an unusual language/runtime failure, the optional cookbook offers additional recipes: [python](../../../.repair-memory/cookbooks/python.md), [go](../../../.repair-memory/cookbooks/go.md). Select a recipe by its trigger; do not load the whole memory layer by default.


<a id="persistence-paths"></a>

## Check shared persistence paths and policy at the boundary

Changing one document representation can affect several codecs, importers, storage paths and query projections. Select a check that follows the value into persistence and back to the public consumer, plus an unchanged document type sharing the processor. Testing only the edited codec can make generation and verification share the same mistake.

Place a policy at every boundary it promises to govern. If direct cache-write helpers are public/testable operations covered by a no-store contract, a guard only in one retrieval caller is insufficient. Enumerate actual write operations and classify intentional exemptions; do not assume every caller must share the same policy. For file writes, distinguish in-place truncation from temp-file replacement. Preserve required mode/ownership on the new inode before publication, and verify existing-mode versus new-file creation behavior separately. Avoid a world-readable publication window followed by chmod as a blanket permission fix.

<a id="cache-error-and-callers"></a>

## Classify real errors and preserve cache scope

Classify cacheable failures from the actual public error representation: application code, HTTP status, typed error and transport failure are not interchangeable. Inspect both the production adapter and available mocks. Do not add broad message-text matching solely to make an unrealistic mock pass; use a message fallback only when that representation is part of the supported contract. Test one permanent failure, one transient failure and successful retry to establish retention policy.

Trace every relevant caller when wrapping an operation. Document intentionally uncached paths rather than declaring every unwrapped call a defect. Cache ownership/lifetime matters: a hook-local map recreated on render may not preserve entries across calls, while a module-global cache may leak across users. Check the repeated operation through the real owner and lifecycle, not two calls to an isolated helper.

<a id="batch-identity"></a>

## Match batch lookup keys and forwarding contracts

For per-entity metadata accompanying a batch, use the same identity function at insertion and lookup, including the documented treatment of composite IDs. Avoid assuming a tuple's last component is globally unique. Carry the map separately through the existing positional or named interface, including explicit absence when the public contract requires it. Check mixed entities, duplicate components from different collections, missing keys and no-map calls. Do not infer runtime behavior from a clean TypeScript build; execute the lookup and downstream consumer in a focused test when available.
