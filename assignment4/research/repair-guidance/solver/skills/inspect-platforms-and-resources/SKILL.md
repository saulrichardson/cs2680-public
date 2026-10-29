---
name: inspect-platforms-and-resources
description: Investigate platform-specific collection, native interfaces, process outcomes, and resource lifecycle. Use when installed and active state differ, sources disagree, cleanup is incomplete, protocols vary by platform, or local environment assumptions affect the repair.
---

# Observe the platform and the resource lifecycle

Platform work often combines several kinds of identity: the source that reports a value, the resource the value describes, the version or variant currently active, and the owner responsible for releasing it. Use this skill when treating those identities as interchangeable creates wrong inventory, forwarding, cleanup, or process behavior.

## Distinguish requested sources, physical sources, and records

A logical source can be named twice, two paths can resolve to the same physical file, and two different collectors can report the same resource. These are different duplicate problems. Preserve the requested source order and reporting label while applying read-once or record-level deduplication only where the public policy calls for it.

A parser chain needs evidence that a parser recognized the intended format. A permissive column parser that skips every line of a stanza file and returns empty success can prevent the correct parser from trying. Keep a valid empty source, unsupported format, malformed content, and failed collection distinct. Source precedence and fallback timing can determine which record wins even when each parser is individually correct.

Enrichment may occur after filtering or for every record, depending on the contract. An optional label lookup should not accidentally turn a successful primary collection into total failure. Conversely, returning a cached host-specific lookup forever can hide changes that callers are allowed to observe. Test with controlled source text and substituted collectors rather than relying on the machine where the test happens to run.

The [inventory and advisory reference](references/inventory-and-advisories.md) develops source resolution, symlinks, parser fallthrough, architecture fields, active package identity, repositories, and per-package advisory state.

## Model active identity rather than a convenient name

Installed latest and currently running are different facts. A package can match a broad family name while representing an accessory, another architecture, an inactive version, or a different debug or real-time variant. Construct identity from the fields that the ecosystem and task use: family, distribution release, package version, release, architecture, variant, and source/binary grouping where applicable.

Preserve complete release strings until the appropriate parser interprets them. An eager numeric conversion can discard a legitimate suffix or stream label. Use the project's native version semantics where required; generic lexical comparison or another ecosystem's version library can select the wrong package. Lifecycle support decisions also need the specified reference time rather than today's date unless the public operation is explicitly time-dependent.

For advisory matching, retain repository and module-stream context through request construction, local lookup, remote lookup, and final reporting. A per-package resolution state must stay associated with that package; a single shared accumulator can produce results that change with iteration order. Use two packages with different states and inspect both final entries.

## Follow acquisition through confirmation, rollback, and release

Creating a native credential or allocating a temporary resource may happen before remote registration or confirmation. If confirmation fails, determine who can roll back the local resource and how that authority reaches the caller. Returning only the success value can leave a caller unable to clean up a partially completed operation.

A useful API can carry a cleanup or rollback action along with the created resource, but its exact form should follow the existing architecture. Verify which path invokes it, whether success transfers ownership, and how repeated or partial cleanup is reported. Do not infer that an error return means no effect occurred. Receipts and current state may be needed after an interrupted operation.

Removal includes every artifact the resource owns. A database field being cleared does not establish that timestamped files or alternate formats were removed. Resolve the actual stored identity or reference through the same root and naming rules used by the producer. Derive paths from trusted identifiers according to the application's rules rather than inventing a hardcoded filename pattern. The [resource lifecycle reference](references/resource-lifecycle.md) develops artifact families, rollback, ownership transfer, and post-cleanup consumers.

## Preserve enough state for callers after cleanup

Releasing an object and forgetting that it existed can have different effects. A process registry may need a tombstone or retained outcome so a page can distinguish a cleaned-up process from an unknown identifier. Every consumer that iterates the registry or dereferences an entry must understand the new state.

Native object deletion can be deferred by an event loop. A wrapper becoming unreachable does not prove the native resource was destroyed, and a visible object can outlive the operation that created it. Check the supported ownership and event-delivery mechanisms. Retain a result where callers need to report completion after the underlying resource is gone.

## Verify actual process and protocol behavior

A subprocess's stdin, byte/text mode, session ownership, completion mechanism, and exit status can be relevant even when the command succeeds on one platform. Avoid optional dependencies when the contract requires a standard capability that already provides the needed behavior. Verify the exact exception types and input semantics of the installed interface.

A kernel or native protocol needs the actual framing, field encoding, response matching, acknowledgement, and error behavior. A structure with the right field names is not proof that it was transmitted correctly. Keep platform-specific implementations and unsupported stubs aligned with their public method sets. A successful build on one target does not establish compatibility with another supported target.

Read [native interfaces and process outcomes](references/native-and-processes.md) for descriptor transport, binary framing, Qt typing and ownership, and exit provenance. Use the installed headers or library source when exact constants or layouts matter.

## Keep environment limitations distinct from code behavior

An old tool can reject a harness operation before a model or test ever starts. A fresh grader can lack dependencies that were present in a local cache. A native build can need unavailable headers even when the source correction is plausible. These are real execution outcomes, but they do not identify a semantic failure in the requested algorithm.

Inspect which condition the candidate can legitimately repair and which belongs to the host or packaging. Preserve working caches when an offline environment cannot restore them, and distinguish edits that travel in the artifact from global workarounds. Report the boundary honestly. This prevents both a false success claim and an unjustified conclusion that every failed check is another model reasoning error.
