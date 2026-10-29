---
name: platforms-processes-and-resources
description: Repair system inventory, active package/advisory identity, process communication/outcomes, native interfaces, Qt compatibility, ownership, rollback, and cleanup. Use for platform-dependent behavior or incomplete resource transitions.
---

# Platforms, processes, and resource lifecycle

Keep platform observations, resource identity, ownership, and visible lifecycle state explicit. Installed is not running; a source label is not physical source identity; removing metadata is not releasing a resource; deferred deletion is not immediate destruction; a process exit code is not its exit mechanism.

Use inventory for collection/format/precedence and package/advisory identity, lifecycle for acquisition/transfer/confirm/rollback/artifact families, and the technical platform chapter for subprocess, descriptor, native transport, Qt ownership, and typed-key boundaries. Test public consumers after cleanup and preserve enough outcome data to report or retry partial work. Derive platform-specific values from the current contract and checkout rather than applying another system's defaults.

## Focused boundary checks

- [Observe native resource lifetime and exit provenance](#qt-lifecycle-and-signals)


## Chapter map

- [Platform inventory and advisory matching](#platform-inventory)
- [Resource lifecycle and rollback](#resource-lifecycle)
- [Process, native protocol, and Qt compatibility boundaries](#domain-process-and-qt)

<a id="platform-inventory"></a>

## Platform inventory and advisory matching

Start with the actual data pipeline: source selection, collection, format recognition, parsing, normalization, filtering, enrichment, matching, and reporting. Record which identity and provenance must survive each boundary.

For multi-source facts, distinguish duplicate source requests, physically identical sources, and duplicate records. Preserve the logical source name for reporting even when canonical paths identify already-read files. Apply source precedence and fallback timing explicitly. A fallback that runs before all primary sources can change which record wins.

For format probing, require enough evidence that the parser accepted the intended format. Silently skipping malformed lines can make a permissive parser falsely win before the correct one is tried. Keep malformed input, unsupported format, empty valid source, and source failure distinct according to the public policy.

For running-kernel filtering, construct identity from distribution family/release, running release, package version/release, architecture, variant, and source/binary grouping as applicable. Installed latest is not running. Broad substring matching can select accessories or a debug variant incorrectly. Retain ordinary non-kernel packages unless the contract changes them.

For advisories, preserve repository and module-stream context into lookup and matching. Keep affected, not-yet-fixed, and publisher resolution state as separate per-package values. A global field overwritten by the last package loses information. Match normalization rules to the ecosystem and return metadata through every caller after changing a matcher contract.

Test source precedence, aliases/symlinks, parser fallthrough, missing helpers, timeout policies, active/inactive variants, unrelated similarly named packages, repository/module mismatch, and two packages with different resolution states. Use a fixed reference date for lifecycle checks. Do not encode current support dates or a universal publisher-state policy from one task.

<a id="platform-inventory-mechanism-details"></a>

<a id="platform-inventory-multi-source-facts-and-package-identity"></a>

### Multi-source facts and package identity

<a id="platform-inventory-details-three-deduplication-scopes"></a>

<a id="platform-inventory-three-deduplication-scopes"></a>

#### Three deduplication scopes

A source request can repeat an alias, two logical paths can resolve to one physical file, and two different sources can report one resource. These are not the same duplicate. Preserve requested order, use canonical identity only for the relevant read-once decision, and retain the original label in provenance. Warnings should be owned by the policy that detects a reportable duplicate, not emitted again at every layer.

For an invented inventory, logical `primary` and `snapshot` sources can refer to the same file, while an external command also reports the same mount/resource. Reading the file once does not imply dropping its first logical label. Record-level first-wins, all-occurrences, or merge behavior must follow the public API. Preserve raw source text if consumers use it for diagnostics.

<a id="platform-inventory-details-format-recognition-before-normalization"></a>

<a id="platform-inventory-format-recognition-before-normalization"></a>

#### Format recognition before normalization

Separate line grammars from file-level recognition. A column parser can incorrectly accept a stanza file if it skips every short line and returns empty success. Reject incompatible structure so the next parser can try, while preserving the contract's malformed-line behavior within an accepted format. A discriminator can apply to the whole file even when not every row has the same distinctive token.

Keep explicit unknown/default values different from absent optional columns. Preserve spacing inside fields when it has meaning. Enrichment should happen for records that survive the required filters; an optional UUID/label helper failing need not turn a valid primary record into a total failure unless required. Memoization of a system-wide lookup must have an appropriate lifecycle/reset seam when tests or callers depend on refresh.

Timeout handling can be error, warning, or omission. Apply it per operation under the contract, and validate invalid timeout configuration at the correct boundary. A helper forwarding an explicit value unchanged and a public entry rejecting that value can both be correct. Test with substituted collectors rather than a live host's incidental state.

<a id="platform-inventory-details-running-versus-installed-kernel"></a>

<a id="platform-inventory-running-versus-installed-kernel"></a>

#### Running versus installed kernel

Model package identity as structured data, not a display name. A modern release string may combine version, package release, architecture, and a variant suffix; another family can use a different scheme. Keep full distribution release text until the family/version parser resolves it. Converting it eagerly to an integer can reject legitimate suffixes.

For binary/source package systems, group by source identity and version when the policy is source-scoped. First determine whether a group contains a running image/header binary; then retain/drop related binaries under the family's rules. Filtering solely by source name can merge inactive versions into the running group. Preserve original identity alongside lookup normalization.

Debian versions have ecosystem-specific epoch/upstream/revision comparison semantics. Source: [Debian version policy](https://www.debian.org/doc/debian-policy/ch-controlfields.html#version). Reuse the project's native comparison implementation; lexical or generic semantic-version comparison is not equivalent.

Test an active image, another installed release, matching companion packages, debug/non-debug or real-time variants, another architecture, accessories with similar names, and an ordinary package. Use the current issue to determine exact family rules. “Any package containing kernel” and “highest version wins” are both inadequate general filters.

<a id="platform-inventory-details-advisory-state-is-per-matched-package"></a>

<a id="platform-inventory-advisory-state-is-per-matched-package"></a>

#### Advisory state is per matched package

Keep `(advisory identity, package identity, repository/module context)` associated with affectedness, fix availability, and resolution. Publisher states such as unsupported, deferred, or under investigation are not universally equivalent to unaffected; map them according to the current policy. An absent resolution entry is a separate case. Do not duplicate a second inconsistent policy in the report aggregator.

For modular packages, a module/stream qualifier can be required for matching; a full build/context label and its compatibility prefix are different identities. A repository may distinguish a base channel from optional streams. Carry the data through request objects into both local-database and remote lookup paths. A correct new field unused by one caller still leaves a false match.

Use two packages attached to one advisory with different states and verify both final entries. Reverse iteration order to catch last-writer metadata reuse. Cover mismatched repository, missing module metadata, fixed and not-yet-fixed cases, unknown publisher state, and ordinary non-kernel behavior. Freeze reference dates in lifecycle tests rather than looking up today's support status during a solve.

<a id="resource-lifecycle"></a>

## Resource lifecycle and rollback

<a id="resource-lifecycle-discover-ownership-before-adding-cleanup"></a>

### Discover ownership before adding cleanup

Inventory resources and owners: files, directories, object parents, handles, timers, processes, credentials, and registry metadata as applicable. Trace acquisition, publication, transfer, completion, cancellation, and destruction. A reference is not necessarily an owner, and removing metadata need not destroy the represented resource.

Determine which state must survive successful completion or cleanup. Some consumers require a retained lifecycle record to distinguish “cleaned” from “never existed.” Do not infer deletion policy solely from the name of a cleanup method.

<a id="resource-lifecycle-locate-the-incomplete-transition"></a>

### Locate the incomplete transition

Build an acquire/confirm/rollback/cleanup table. For every step, ask what remains if the next step fails. Inspect the public command and consumers as well as the helper. Search for alternative construction paths and platform-specific implementations.

 For QObject ownership, consult the [Qt lifecycle chapter](#domain-process-and-qt) only if relevant to the checkout's Qt version.

<a id="resource-lifecycle-implement-consistent-transitions"></a>

### Implement consistent transitions

Prefer the repository's existing ownership mechanism: scoped cleanup, defer/finally, RAII, parent ownership, or explicit release. Transfer ownership only after the destination can assume it. Roll back resources still owned locally; leave transferred resources to their new owner.

When order matters, unwind in reverse dependency order, not necessarily reverse textual order. Preserve the primary operation's failure while making cleanup failures observable in the established way. Do not invent silent success or discard useful context.

Make repeated cleanup safe when the public lifecycle permits it. Define how partial cleanup is retried and when the state may become “cleaned.” For a registry, coordinate physical artifacts, in-memory entries, persistent metadata, and consumer lookups; deleting just one can leave inconsistent behavior.

<a id="resource-lifecycle-verify-each-owned-edge"></a>

### Verify each owned edge

Inject failure after each acquisition boundary. Assert surviving artifacts, live children, handles, timer callbacks, and visible metadata, not merely the returned error. Check successful transfer, cancellation, repeated cleanup, and read-after-cleanup. Use the existing platform/toolchain checks for conditionally compiled paths. If the problem is merely stale data with no ownership transition, use the relevant state or persistence chapter.

<a id="resource-lifecycle-mechanism-details"></a>

<a id="resource-lifecycle-ownership-rollback-and-retained-state"></a>

### Ownership, rollback, and retained state

<a id="resource-lifecycle-details-ownership-ledger"></a>

<a id="resource-lifecycle-ownership-ledger"></a>

#### Ownership ledger

Record `resource → creator → current owner → transfer point → release action → surviving metadata`. Include dependencies: a child can depend on a directory, a process on its pipes, or a callback on its owner.

| Transition | Resource responsibility |
|---|---|
| Acquisition fails | Release earlier locally owned acquisitions |
| Acquisition succeeds but publication fails | Roll back unpublished resources |
| Publication transfers ownership | The recipient owns future release; local failure handling must respect that transfer |
| Operation completes | Apply the contract's success policy, which may differ from failure cleanup |
| Cleanup partly fails | Preserve enough state to report/retry; do not falsely mark fully cleaned |

<a id="resource-lifecycle-details-rollback-structure"></a>

<a id="resource-lifecycle-rollback-structure"></a>

#### Rollback structure

```text
owned = empty cleanup stack
try:
    acquire resource A; register release(A)
    acquire resource B that depends on A; register release(B)
    validate/confirm
    publish resources to their long-lived owner
    remove transferred resources from local cleanup ownership
    return the required result
on failure:
    preserve the operation error
    release still-owned resources in dependency-safe order
    report cleanup failures using existing conventions
```

An explicit stack is illustrative. Existing defer/finally or ownership primitives may express the same invariant more clearly. Avoid a second cleanup system on top of reliable automatic ownership.

<a id="resource-lifecycle-details-worked-case-a-export-to-a-temporary-directory"></a>

<a id="resource-lifecycle-worked-case-a-export-to-a-temporary-directory"></a>

#### Worked case A: export to a temporary directory

An exporter creates a directory, opens an output, writes metadata, then registers the completed export. A failure opening the output must remove the newly created directory if this invocation owns it. A failure writing metadata must close the file before directory removal where the platform requires that. After registration transfers ownership, the enclosing function must not delete a successful export merely because it is returning.

Test a pre-existing shared directory separately. An invocation does not own it simply because it wrote a file inside it.

<a id="resource-lifecycle-details-worked-case-b-process-supervision"></a>

<a id="resource-lifecycle-worked-case-b-process-supervision"></a>

#### Worked case B: process supervision

A supervisor owns a child process, output streams, and a status record. After exit, live handles can be released while an exit-status record remains readable. If callers distinguish “completed and cleaned” from “unknown job,” deleting the entire record is incorrect. Mark the established lifecycle state and release physical resources according to their owners.

Check all consumers that list, retrieve, cancel, or inspect the job. A helper-only test can miss the visible-state regression.

<a id="resource-lifecycle-details-counterexample-borrowed-objects"></a>

<a id="resource-lifecycle-counterexample-borrowed-objects"></a>

#### Counterexample: borrowed objects

A function temporarily uses a shared connection or an externally parented object. Adding unconditional destruction in its local `finally` can break later consumers. Release only resources whose ownership was acquired, or relinquish a borrow using the API's actual contract.

<a id="resource-lifecycle-details-verification"></a>

<a id="resource-lifecycle-verification"></a>

#### Verification

Force failure after each acquisition, before/after transfer, and during cleanup itself. Assert no unexpected artifact, callback, or owned child survives. Assert required metadata does survive. Exercise repeated cleanup only where supported and test the failure/partial state so it cannot be mistaken for completion. Verify success and failure paths independently; “always delete everything” often makes one correct by breaking the other.

<a id="resource-lifecycle-details-artifact-families-references-and-replacement"></a>

<a id="resource-lifecycle-artifact-families-references-and-replacement"></a>

#### Artifact families, references, and replacement

An image or generated export can be a family: original upload, resized copies, crop output, thumbnail, cached metadata, and persistent references. Removing one visible URL does not remove the family. Enumerate creation and replacement paths, including a separate owner type that may use the same helpers. Centralize removal where the ownership and local-path policy is known, not at a UI callback that sees only one filename.

Distinguish a local owned upload from a remote URL, a shared artifact, and a missing local file. Parse and validate a path using existing repository rules before deletion; do not derive ownership from a string prefix that can be ambiguous after encoding or normalization. Support the extensions/variants promised by the contract without a broad filesystem sweep that deletes another owner's files.

A replacement can need two phases: create/validate the new artifact and publish its reference, then retire the old family under the required consistency policy. Deleting old data before the new data is usable can lose a valid image on failure. Publishing a new reference without cleaning owned old variants can leak files. The actual transaction and failure guarantees determine ordering and rollback; do not invent global atomicity where it is not available.

For removal, decide how database field clearing and file cleanup coordinate. Missing files may be an idempotent success under the public policy; permission failure can be a different error. Keep enough state to report/retry partial cleanup. The meaningful completion condition includes references and owned physical files, not only a returned success flag.

An invented profile image has an original and two generated sizes. Test upload, crop, replacement, explicit removal, and owner deletion through their public entry points. Seed an unrelated remote URL and another owner's files to ensure they survive. Inject failure between creation, publication, and retirement. Assert the complete remaining artifact set and persistent references.

<a id="resource-lifecycle-details-confirmable-acquisition"></a>

<a id="resource-lifecycle-confirmable-acquisition"></a>

#### Confirmable acquisition

Some APIs create a provisional credential/resource and return an object that can confirm or roll back. The caller must retain that handle across later validation; returning only the creation payload loses the cleanup capability. Identify every failure after acquisition and route it through rollback when ownership remains provisional. Confirm preserves the resource; rollback releases it according to its actual identifier and backend.

Exercise a failure after creation but before confirmation, successful confirmation followed by use, rollback followed by a missing-resource lookup, and any documented repeat behavior. Do not change the creation payload's serialization while wrapping it in a lifecycle object if downstream protocol parsing must still accept it.

<a id="resource-lifecycle-artifact-families-and-native-boundaries"></a>

### Artifact families and native boundaries

Read process and Qt boundaries for subprocess communication, outcome reporting, event-loop destruction, and typed Qt conversions. For generated media, treat originals, derivatives, persistent references, and replacements as one ownership graph; use the artifact-family section of the linked ownership reference.

<a id="domain-process-and-qt"></a>

<a id="domain-process-native-protocol-and-qt-compatibility-boundaries"></a>

### Process, native protocol, and Qt compatibility boundaries

Use when behavior varies by platform/dependency, process outcomes are misreported, or typed Qt APIs replace integer conventions. Facts from the linked APIs are followed by authored repair reasoning; use the versions pinned by the checkout.

<a id="domain-process-and-qt-separate-process-outcomes"></a>

<a id="domain-separate-process-outcomes"></a>

#### Separate process outcomes

Model spawn failure, running, ordinary exit, signal/crash, timeout, and explicit cancellation. Exit code and exit mechanism are separate. Preserve outcome data before cleanup destroys the process object. Presentation policy—verbosity, warning/error level, and whether success is silent—belongs in an explicit outcome-by-mode table. Do not turn every nonzero code into the same user message or treat a crash as an ordinary successful exit because a stale code field is zero.

Track PID, pipes, callbacks, timers, temporary credentials, and lifecycle metadata with separate owners. An object that is safe to release may still have a required registry tombstone. Test commands, status pages, completion providers, and lookups that consume that state, not only the exit callback.

<a id="domain-process-and-qt-python-subprocess-communication"></a>

<a id="domain-python-subprocess-communication"></a>

#### Python subprocess communication

Python provides pipe-aware communication and distinct process-creation/timeout errors. Waiting while undrained pipes fill can deadlock. Multiplexing support differs by platform; the high-level selectors interface does not make Windows pipes behave like Unix descriptors. Sources: [subprocess](https://docs.python.org/3/library/subprocess.html), [selectors](https://docs.python.org/3/library/selectors.html).

For a bounded one-shot exchange, understand whether the current API can own input delivery, output collection, and waiting as one operation. For streaming interaction, choose the repository's platform-capable I/O mechanism. A workaround based on `select` may fail for high descriptor numbers or unsupported file kinds. Installing an optional library is not a repair when the contract requires consistent behavior without it.

Separate argv tokenization from shell execution. A shell-like option string can be parsed into argument tokens without launching a shell. Preserve executable selection, argument ordering, and any caller-required invocation seam. Environment inheritance is a policy: sometimes only a narrow environment is permitted, elsewhere preserving inherited variables is required. Do not choose one universally.

For password or token input, use harmless synthetic values in tests. Redact the actual secret from stderr before composing public diagnostics, while preserving required decoding/whitespace/error behavior. Avoid treating a failed spawn as a child exit; it has no ordinary child status. Clean temporary resources along every ownership path.

<a id="domain-process-and-qt-qt-object-and-event-loop-lifetimes"></a>

<a id="domain-qt-object-and-event-loop-lifetimes"></a>

#### Qt object and event-loop lifetimes

QObject parent ownership and deferred deletion are different mechanisms. `deleteLater()` schedules destruction through event-loop/lifecycle behavior; calling it does not prove immediate destruction. Qt documents special cases for stopped or absent event loops. Source: [QObject](https://doc.qt.io/qt-6/qobject.html), with [object ownership](https://doc.qt.io/qt-6/objecttrees.html).

Associate callbacks and timers with the correct owner/context. Inspect whether releasing a registry entry actually releases parented objects, whether a live timer can recreate work, and whether a callback dereferences an object after its lifetime ends. Do not add a second owner or unconditional destructor merely because a raw pointer exists.

Qt's QProcess distinguishes process error signals, exit status, and exit code; its documentation defines when outcome values are meaningful. Source: [QProcess](https://doc.qt.io/qt-6/qprocess.html).

Use those separate axes to construct the reporting table. Preserve the outcome to downstream consumers before scheduling cleanup. Exercise failed start, nonzero ordinary exit, crash/signal, successful exit, and configured verbosity as relevant. Timer-driven cleanup should act on the original process identity, not whichever process later reused a display slot.

<a id="domain-process-and-qt-typed-keys-across-qt-versions"></a>

<a id="domain-typed-keys-across-qt-versions"></a>

#### Typed keys across Qt versions

Qt 6 introduces QKeyCombination, representing a key and modifiers, with `key()`, `keyboardModifiers()`, `toCombined()` and `fromCombined()`. It is not simply interchangeable with every integer operation. Source: [QKeyCombination](https://doc.qt.io/qt-6/qkeycombination.html).

Choose a semantic internal representation and adapt at version boundaries. Inventory key parsing, modifier changes, equality, hashing, iteration, serialization, and event conversion. Replacing one cast can leave arithmetic/bitwise assumptions elsewhere. Preserve explicit default modifiers and ensure comparisons operate on equivalent semantic types. Use supported conversions instead of reaching into undocumented bit layouts.

Construct semantic round trips: parse a key/modifier combination, inspect its parts, serialize, read it again, and compare meaning. Test a modifier-free key and multiple modifiers. Gate version-specific APIs at the boundary; do not scatter inconsistent conversions through every caller or upgrade Qt just to make one API available.

<a id="domain-process-and-qt-native-binary-protocol-adapters"></a>

<a id="domain-native-binary-protocol-adapters"></a>

#### Native binary protocol adapters

For a native status/event interface, separate portable event contracts from platform transport. Fixed binary layouts need fixed-width fields and the protocol's byte order. Kernel/library-owned length, sequence, and process fields should not be invented by an application. The number and ordering of replies, acknowledgements, and events follows the actual transport API; waiting for an extra acknowledgement that will never arrive can hang an otherwise valid exchange.

Source for Netlink exchange distinctions: [Linux Netlink introduction](https://www.kernel.org/doc/html/latest/userspace-api/netlink/intro.html); classic and generic families can differ, so match the actual family/library contract.

Put transport behind the existing injectable interface. Test status-disabled, connect failure, malformed status, send failure, and success with recorded messages. Observe whether a disabled subsystem emits zero events and whether enabled operation emits the required number. Keep unsupported-platform behavior explicit and run available compile checks for alternate files. Never replace the real dependency with a locally invented lookalike merely to make tests compile.

## Optional deeper cookbook

The mechanism and required technical facts are in this file. For an unusual language/runtime failure, the optional cookbook offers additional recipes: [native](../../../.repair-memory/cookbooks/native.md), [python](../../../.repair-memory/cookbooks/python.md). Select a recipe by its trigger; do not load the whole memory layer by default.


<a id="qt-lifecycle-and-signals"></a>

## Observe native resource lifetime and exit provenance

Qt timers, child processes, QObject ownership, Python references and deferred deletion are different lifetimes. `deleteLater()` schedules deletion when Qt processes the relevant deferred event; it does not guarantee destruction at the next arbitrary event-loop iteration. Stop or await owned work according to the lifecycle contract, then schedule destruction when safe. Do not blindly kill processes or delete parent-owned objects merely because a collection removed a reference. `del` only changes a Python reference; binding ownership rules determine whether that also destroys a native object.

A focused check observes both the required stop/finished condition and actual object destruction while allowing the appropriate event loop to progress. A parent can own deletion; duplicate cleanup should not access an already deleted object. Test required idempotence and failure-before-start separately.

For exit reporting, retain QProcess error, exit status and exit code separately. A normal exit code is not a signal number. Only use `signal.Signals(code).name` when the platform/API contract actually establishes that code is a signal; catch unknown values. Qt's cross-platform crash status does not itself guarantee Unix signal-number encoding. Preserve raw values and a neutral fallback when provenance is unclear.
