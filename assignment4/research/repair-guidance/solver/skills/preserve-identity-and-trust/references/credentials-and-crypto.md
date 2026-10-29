# Credentials, trust, and paired cryptographic material

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="domain-identity-and-credentials"></a>

<a id="domain-identity-authorization-trust-and-credential-transport"></a>

### Identity, authorization, trust, and credential transport

Use when values cross trust/identity boundaries or a credential abstraction replaces direct material. These are repair models, not a new security policy. The current public contract and existing cryptographic libraries determine valid algorithms and choices.

<a id="domain-identity-and-credentials-separate-four-identities"></a>

<a id="domain-separate-four-identities"></a>

#### Separate four identities

Record actor (who is authenticated), target (whose object changes), action (what is requested), and resource/key identity (what authorizes or decrypts it). A self-service operation and an administrator acting on another user can require different checks. A UI hiding an option is not an authorization boundary; exercise the public route/service and assert unchanged state on rejection.

Request-only verification data should not be persisted as an ordinary profile field. Distinguish “no secret change requested,” “change requested but verification absent,” “verification incorrect,” and “verification valid” under the actual policy. Do not reject unrelated updates by applying a secret-change check unconditionally. Preserve the project's error classification and response mapping.

<a id="domain-identity-and-credentials-transported-keys-are-not-necessarily-usable-keys"></a>

<a id="domain-transported-keys-are-not-necessarily-usable-keys"></a>

#### Transported keys are not necessarily usable keys

A caller can supply owner-encrypted key bytes that must still be resolved through the correct owner/group key. Transport, resolution, and trust preference selection are separate. Preserve raw byte identity when the interface promises it; base64 is a representation change, not a harmless refactor. In tests use distinct marker buffers and inspect the final consumer without logging actual secrets.

Single and batch loaders may travel through different wrappers. Bind each entity to its own supplied value by the documented entity identity, not the outer container identity or previous iteration. Resolve each independently unless sharing is explicitly valid. Removing an unsafe shared cache without repairing the forwarding chain can simply expose the missing value elsewhere.

An absent argument and an explicit null can trigger different paths. A wrapper forwarding positional arguments must preserve their slots even when a middle optional value is absent. After changing signatures, inspect direct callers, cache decorators, facades, indexing, and background processing as interface consumers, without using a predetermined target-file list.

<a id="domain-identity-and-credentials-trust-preferences-require-a-matrix"></a>

<a id="domain-trust-preferences-require-a-matrix"></a>

#### Trust preferences require a matrix

Keep presence/absence of usable material separate from preferences about its use. Pinned/trusted material, discovered/untrusted material, contact type, explicit user preference, and absent preference can select different branches. Do not merge two preference fields merely because both ultimately influence encryption.

Construct the decision table from existing public policy. Record selected material, encryption/signing mode, required failure, and where the preference originated. Test one material source at a time and competing sources together. An explicit choice must not be overwritten by an “available key” shortcut. This reference does not prescribe a default trust decision for an unknown application.

<a id="domain-identity-and-credentials-key-store-abstraction-and-paired-material"></a>

<a id="domain-key-store-abstraction-and-paired-material"></a>

#### Key-store abstraction and paired material

An opaque key identifier may refer to hardware state, a software key, or a serialized handle. Consumers should request the capability they need—a signer, certificate pair, or deletion—through the store's actual interface. Do not assume all identifiers can be parsed as a private key, or that all backends implement deletion with the same effect.

Preserve key/certificate pairing and purpose when selecting among entries. Choosing a valid private key from one entry and certificate bytes from another can compile but fail cryptographically. Test a produced signature with an independent verifier appropriate to the existing algorithm, and test retrieval using the returned identifier. Cover mixed backend entries, absence of eligible material, and the documented passphrase/generator injection path.

<a id="domain-identity-and-credentials-registry-credential-providers"></a>

<a id="domain-registry-credential-providers"></a>

#### Registry credential providers

Static credentials and dynamically resolved registry credentials have different lifetimes and configuration requirements. A provider callback can resolve for the registry at use time, rather than freezing an ambient token during construction. Preserve optional authentication when supported and do not require static fields for a dynamic mode.

AWS ECR authorization tokens are obtained through its API and base64-decode to a username/password pair with username `AWS`; their permissions reflect the retrieving principal. Split the decoded pair at its first colon so a later colon remains part of the password. The documented token lifetime is 12 hours; use actual provider expiry/refresh semantics rather than treating it as a permanent IAM credential. Source: [ECR authentication](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html).

For a provider, distinguish SDK/configuration failure, API error, absent authorization data, missing token, malformed encoding, missing separator, and valid credentials. A token containing a password with a delimiter must be split using the credential format's rule, not indiscriminately into arbitrary pieces. Preserve underlying errors where the public API requires it. Use the real pinned SDK interface and an injectable client; a locally invented SDK substitute can hide a compile-time incompatibility.

<a id="domain-identity-and-credentials-signed-identity-versus-presentation"></a>

<a id="domain-signed-identity-versus-presentation"></a>

#### Signed identity versus presentation

JWT is a claims representation carried in a signed or encrypted structure; decoding claims alone does not establish authenticity. Source: [RFC 7519](https://www.rfc-editor.org/rfc/rfc7519).

When separating resource identity from presentation options, trace claim construction, validation, typed extraction, caller propagation, and URL/rendering use. A valid signature with a missing or wrong-typed required claim must still follow the required failure policy. A display size, label, or formatting option need not change stable resource identity. Preserve the intended compatibility with old tokens explicitly rather than accepting every decoded shape.

<a id="domain-identity-and-credentials-independent-evidence"></a>

<a id="domain-independent-evidence"></a>

#### Independent evidence

Use a matrix of actor/target/action, direct/wrapped/batch calls, two distinct entities and a missing value, trust branches, and signed valid/malformed/wrong-type data. Observe actual consumer arguments and persisted/public effects. Crypto tests should verify behavior through existing primitives; reimplementing cryptography in a repair test can repeat the implementation's assumptions.

<a id="cross-layer-value-propagation"></a>

## Cross-layer value and identity propagation

<a id="cross-layer-value-propagation-establish-value-semantics"></a>

### Establish value semantics

Trace producer → forwarding signatures → wrappers → actual consumer. Record the value's type, encoding, presence semantics, scope, and selection policy. Distinguish missing, explicit null/empty, zero, and supplied values when the interface does. For opaque key material, transport and preference/trust selection are separate responsibilities.

Discover whether a supplied value is already decoded, encrypted transport, a lookup handle, or the final value. Do not transform opaque bytes or introduce a trust fallback because an intermediate layer lacks context.

<a id="cross-layer-value-propagation-expose-the-missing-or-incorrect-edge"></a>

### Expose the missing or incorrect edge

List direct calls, decorated calls, single-entity calls, and batch calls. Search all implementations and test doubles when changing a signature. In a batch, inspect the lookup key used for every entity; a correct value for one entity can conceal accidental reuse for the next.

<a id="cross-layer-value-propagation-repair-the-full-chain"></a>

### Repair the full chain

Add the value at the established interface boundary and forward it unchanged until the component responsible for interpretation. Preserve omitted-value compatibility. If absence should trigger resolution, leave that decision to the designated resolver rather than silently substituting a default in every wrapper.

For batch handling, bind each entity's identity, supplied value, and resolved value within that iteration or its own result slot. In asynchronous code, ensure callbacks capture the intended entity. A shared “last resolved value” is not an identity map.

For preference/trust branches, make a decision table using the actual public rules. Preserve explicit caller choices and required missing-value failures. Do not return another entity's cached value or an unverified fallback as a way to avoid an error.

<a id="cross-layer-value-propagation-verify-through-consumers"></a>

### Verify through consumers

Use distinct marker values for two entities, one missing value, a direct call, and the wrapper path. Assert raw equality where required, correct argument presence/position, and observed consumer behavior. Test supported batch orderings to expose state reuse. In Go, run relevant interface/build checks; updating the primary implementation alone may leave alternate implementations uncompilable. If the issue is configuration decoding rather than forwarding, apply the configuration chapter too.

<a id="cross-layer-value-propagation-mechanism-details"></a>

<a id="cross-layer-value-propagation-presence-opaque-values-and-per-entity-resolution"></a>

### Presence, opaque values, and per-entity resolution

<a id="cross-layer-value-propagation-details-preserve-meaning-until-the-responsible-layer"></a>

<a id="cross-layer-value-propagation-preserve-meaning-until-the-responsible-layer"></a>

#### Preserve meaning until the responsible layer

Write a ledger for each edge: signature, received value, forwarded value, and consumer. An optional argument can be syntactically accepted yet ignored by an adapter. An empty value can be mistakenly replaced by a default through truthiness rather than an explicit presence test.

```text
for entity in entities:
    supplied = lookup supplied values using entity's actual identity
    if supplied is present under the interface's presence rules:
        resolved = interpret supplied only at the designated resolver
    else:
        resolved = perform that entity's permitted resolution
    consume(entity, resolved, its explicit preferences)
```

The algorithm does not prescribe how cryptographic resolution or trust should work. Use the project's actual APIs and policies. Never log secrets to prove propagation; test with harmless synthetic marker bytes.

<a id="cross-layer-value-propagation-details-worked-case-a-encrypted-document-loader"></a>

<a id="cross-layer-value-propagation-worked-case-a-encrypted-document-loader"></a>

#### Worked case A: encrypted document loader

A caller supplies a document-scoped opaque byte value through a caching wrapper into a loader. The wrapper originally forwards only the document ID. A direct loader test passes, but the public path loses the value. Add a spy at the resolver and invoke the wrapper. Verify the same bytes reach it without string conversion, re-encoding, truncation, or accidental use as a different entity's bytes.

If the argument is optional, also test the established resolution path when it is absent. The fix must not turn all formerly valid calls into required-argument failures.

<a id="cross-layer-value-propagation-details-worked-case-b-batch-import-with-per-item-options"></a>

<a id="cross-layer-value-propagation-worked-case-b-batch-import-with-per-item-options"></a>

#### Worked case B: batch import with per-item options

An importer processes two records with distinct supplied metadata and a third with none. Run both record orders. Each consumer must receive the value associated with that record, and the missing record must follow its own fallback/error policy. A variable declared outside the loop and assigned only when a value is present can reuse the previous record's value.

Asynchronous closures can create a similar error even when the map lookup is correct. Observe the entity/value pair at the final consumer after controlled out-of-order completion.

<a id="cross-layer-value-propagation-details-counterexample-arbitrary-fallback"></a>

<a id="cross-layer-value-propagation-counterexample-arbitrary-fallback"></a>

#### Counterexample: arbitrary fallback

A resolver cannot find an eligible value for entity B. Reusing entity A's successful result may make a happy-path test pass while violating identity and access semantics. Preserve the specified missing-value error or authorized alternative. Treat caching as another consumer of identity, not as permission to share values globally.

<a id="cross-layer-value-propagation-details-verification-matrix"></a>

<a id="cross-layer-value-propagation-verification-matrix"></a>

#### Verification matrix

Cover direct/wrapped, single/batch, absent/present/malformed where defined, two distinct entities, and a missing entity after a present one. Check explicit preference versus omitted preference separately from key/value transport. Assert final consumer observations and public errors. For changed interfaces, inspect generated adapters and platform variants; regenerate generated files using the existing repository process rather than editing only their outputs.

<a id="cross-layer-value-propagation-trust-and-usable-material"></a>

### Trust and usable material

For owner-encrypted material, per-entity key resolution, paired certificates/signers, or separate trust preferences, read [identity and credentials](#domain-identity-and-credentials). Preserve the distinction between transport bytes, resolved key, and policy choice; forwarding a value does not prove it was interpreted correctly.

<a id="identity-and-propagation"></a>

## Preserve identity, provenance and argument positions

Treat an opaque path or entity ID as opaque: capture the full promised value and forward it unchanged unless normalization is explicitly part of the API. For discriminators stored alongside raw material, establish whether metadata is authoritative, inferred, stale or untrusted before choosing it over parsing. Avoid redundant reclassification when a trusted validated discriminator already supplies the answer; do not blindly trust arbitrary metadata.

Request-context users and persisted users have different provenance. Identify which fields are authoritative for the operation; a context object may already be validated, while persistent credential validation may require the current stored record. An extra database read is not automatically safer, and client-supplied fields are not automatically authoritative.

When adding an optional positional argument, preserve every downstream slot through wrappers. Omitting a trailing argument does NOT shift earlier arguments in JavaScript; however explicit `undefined` may be part of an observable call contract, and omitting an interior argument does shift later ones. Test absence and presence through each actual wrapper, and distinguish value semantics from `arguments.length` or mock call-tuple assertions when those are contractual.
