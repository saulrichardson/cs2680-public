# Acquisition, transfer, rollback, and retained outcomes

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="resource-lifecycle"></a>

## Resource lifecycle and rollback

<a id="resource-lifecycle-discover-ownership-before-adding-cleanup"></a>

### Discover ownership before adding cleanup

Inventory resources and owners: files, directories, object parents, handles, timers, processes, credentials, and registry metadata as applicable. Trace acquisition, publication, transfer, completion, cancellation, and destruction. A reference is not necessarily an owner, and removing metadata need not destroy the represented resource.

Determine which state must survive successful completion or cleanup. Some consumers require a retained lifecycle record to distinguish “cleaned” from “never existed.” Do not infer deletion policy solely from the name of a cleanup method.

<a id="resource-lifecycle-locate-the-incomplete-transition"></a>

### Locate the incomplete transition

Build an acquire/confirm/rollback/cleanup table. For every step, ask what remains if the next step fails. Inspect the public command and consumers as well as the helper. Search for alternative construction paths and platform-specific implementations.

 For QObject ownership, consult the [Qt lifecycle chapter](native-and-processes.md#domain-process-and-qt) only if relevant to the checkout's Qt version.

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
