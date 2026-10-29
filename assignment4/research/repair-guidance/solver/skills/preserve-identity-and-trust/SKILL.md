---
name: preserve-identity-and-trust
description: Preserve entity identity, credential purpose, authorization, and trust decisions across storage and forwarding. Use for cryptographic key operations, signed identifiers, password verification, registry credentials, or related entities whose secrets or preferences can be confused.
---

# Keep identity, usable material, and permission separate

A credential being available, a cryptographic operation succeeding, and a caller being authorized are different conditions. Use this skill when data crosses a trust or identity boundary, especially when an abstraction, cache, or forwarding layer can mix values that belong to different purposes or entities. The objective is to preserve the actual security and compatibility contract while making the data flow observable.

## Establish what each identifier names

Distinguish the caller, the resource, the lookup key, the signed subject, the presentation parameters, and the material used to prove identity. Similar strings or shared storage do not make those identities equivalent. An artwork or document identifier can name a stable resource while size or formatting belongs to a separately mutable presentation choice. A key identifier can select one store entry while a certificate must remain paired with the private key in that entry.

Trace identifiers from construction through serialization, signing, decoding, selection, and final use. Determine whether a value is opaque transport, decoded key bytes, an encrypted wrapper, or a handle for later resolution. Avoid interpreting or normalizing opaque values at an intermediate layer merely because their format looks familiar.

A token's validity does not establish that it contains required claims of the correct type. A decoded claim can be structurally valid yet invalid in the application domain. Preserve the required distinctions among malformed transport, failed verification, missing fields, incorrect types, and invalid business values. The consumer may rely on the resulting error category or message.

## Respect the purpose and pairing of cryptographic material

An abstraction over several key stores should expose the capability callers need while preserving how each backend generates, selects, signs, decrypts, and releases material. Check all implementations and callers when changing its method set. A fallback that silently generates a new key can hide a retrieval error or break a caller's stable identity.

Verify a produced signature with an independent verifier appropriate to the installed algorithm and key type. Verify retrieval using the identifier returned by creation. Test a collection containing mixed entry types so selection cannot accidentally take a key from one entry and a certificate from another. Where a passphrase provider or generator is injected, observe that the designated provider is actually used rather than replaced by a convenient default.

The [credential and cryptographic boundaries reference](references/credentials-and-crypto.md) develops paired material, provider modes, trust selection, signed identity, and direct verification. Its examples describe capabilities rather than requiring a particular cryptographic library or backend.

## Carry entity-specific secrets through the complete loading path

Related entities can share key material without sharing loading order. A cache populated when a parent loads may be absent when a child loads independently. If a caller already holds the required material, an interface may need to forward it explicitly through wrappers, batch loaders, and resolvers. The contract decides whether absence triggers discovery, failure, or another supported resolution path.

Preserve parameter positions and meanings through every forwarding layer. In a batch, associate each entity with its own supplied and resolved values. A shared last-resolved value or conditionally populated global map can make the result depend on processing order. Use distinct marker values for two entities and exercise direct, wrapped, and batch loading. Reverse order where the result should be independent of it.

Do not introduce an unverified trust fallback to compensate for missing context. Transporting material and selecting whether that material may be used are separate responsibilities. See [configuration and interface propagation](../trace-configuration-and-interfaces/SKILL.md) for the mechanics of carrying presence and identity through changed signatures.

## Represent policy choices explicitly enough to distinguish them

User preference, contact type, trust status, available material, and algorithm support can select different branches. A single boolean such as “secure” or “has key” can collapse combinations with different required outcomes. Derive the decision cases from the task and current policy. Include the missing-material path and the explicit user choice that must survive intermediate defaults.

A policy may prioritize trusted pinned material over discovered material, or require failure when a requested mode cannot be satisfied. Another policy may deliberately support negotiation or fallback. The existence of one successful fallback in another task is not a reason to add it here. Observe which branch is chosen and what reaches the actual encryption, signing, or verification consumer.

## Check authorization through the public operation

A password-change helper can correctly compare two values while the endpoint never supplies the current password. A request object can contain the field under a name the backend ignores. Trace the UI or caller input through serialization and authentication into the operation that checks the existing credential, then follow errors back to the user-visible response.

Validation order can be consequential. A failed prerequisite may need to surface before any database or remote effect. Obtain the current user or provider through the lookup seam the application actually promises, including call-time resolution when tests or request scope depend on it. A module-level cached identity can belong to another request or prevent supported replacement of the provider.

Use a permitted caller, a caller lacking the necessary proof, and the relevant invalid or absent input. Verify both the rejected effect and the required response. A method's existence or a UI field's presence is useful source evidence but does not establish enforcement.

## Preserve the boundary between identity and presentation

A signed stable identifier need not contain every parameter used to render or fetch the resource. If the task separates mutable detail from identity, follow the new detail through URL construction, parsing, and the final transformation. Preserve independent behavior such as output format or downscale-only rules where required. A round-trip of the token does not establish the consumer's image or document behavior.

Do not generalize this into removing parameters from every token. Some systems deliberately sign a size, expiry, audience, or operation to limit authority. The correct design follows what the signed statement is intended to authorize. Verify the allowed operation with the decoded identity and constraints, rather than assuming that cryptographic validity alone settles the application contract.
