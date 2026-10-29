# Cache identity, policy, and retained outcomes

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

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

Read [cache storage and policy](#domain-cache-storage-and-policy) when the marker controls cache serializers or disk state. Read [service wiring](../../trace-configuration-and-interfaces/references/service-construction.md#domain-service-wiring) when it controls protocol propagation or runtime dependency construction. A marker assertion alone cannot establish the downstream behavior.

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

<a id="cache-error-and-callers"></a>

## Classify real errors and preserve cache scope

Classify cacheable failures from the actual public error representation: application code, HTTP status, typed error and transport failure are not interchangeable. Inspect both the production adapter and available mocks. Do not add broad message-text matching solely to make an unrealistic mock pass; use a message fallback only when that representation is part of the supported contract. Test one permanent failure, one transient failure and successful retry to establish retention policy.

Trace every relevant caller when wrapping an operation. Document intentionally uncached paths rather than declaring every unwrapped call a defect. Cache ownership/lifetime matters: a hook-local map recreated on render may not preserve entries across calls, while a module-global cache may leak across users. Check the repeated operation through the real owner and lifecycle, not two calls to an isolated helper.
