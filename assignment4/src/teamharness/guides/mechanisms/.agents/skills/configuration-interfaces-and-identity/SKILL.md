---
name: configuration-interfaces-and-identity
description: Wire decoded configuration, schemas, interfaces, and supplied values into real consumers while preserving presence, scope, trust, and lifecycle. Use for provider factories, telemetry, routing, credentials, keys, signed claims, or wrapper migrations.
---

# Configuration, interfaces, credentials, and identity

Follow the contract through schema, decoding, defaults, validation, options, construction, and consumption. Preserve omission until the authorized defaulting layer. A constructor can correctly reject an unresolved value even when the loader supplies a compatibility default. A supplied key is not automatically a usable key or permission to select a trust policy.

Use the wiring chapter to find the first broken edge, value propagation to preserve direct/wrapped/batch arguments, schemas/types for real decoded representations and coercion, identity/credentials for actor-target and cryptographic roles, and service construction for telemetry/address/credential/shutdown branches. Observe actual dependencies and public behavior with distinct fakes; declarations alone prove little.

## Focused boundary checks

- [Respect the actual dependency and constructor seam](#integration-seams)
- [Separate lookup time and canonical defaults](#runtime-defaults)
- [Go decoding, pinned types and interface migration](#go-decode-and-types)
- [Worked Go typed-slice decode hook](#go-typed-slice-hook)
- [Schema lookup, presence and decoded defaults](#schema-presence)
- [Preserve identity, provenance and argument positions](#identity-and-propagation)


## Chapter map

- [Configuration and interface wiring](#configuration-runtime-wiring)
- [Cross-layer value and identity propagation](#cross-layer-value-propagation)
- [Presence, schemas, coercion, and representation boundaries](#domain-schema-and-types)
- [Identity, authorization, trust, and credential transport](#domain-identity-and-credentials)
- [Service construction, telemetry, endpoint selection, and shutdown](#domain-service-wiring)

<a id="configuration-runtime-wiring"></a>

## Configuration and interface wiring

<a id="configuration-runtime-wiring-create-a-path-ledger"></a>

### Create a path ledger

Trace schema/documented input → parser → decoded representation → validation → defaults/merge → options → constructor → consumer. Include embedded/generated schemas and alternative constructors when they exist. For each hop record type, name, presence, and effective value.

Distinguish omitted, explicit default, explicit zero/false/empty, valid alternative, and unsupported value. A language zero value does not establish whether the caller supplied the field. Find the layer where absence is intentionally resolved.

<a id="configuration-runtime-wiring-diagnose-the-gap"></a>

### Diagnose the gap

Inject a distinct value at the public configuration entry point and observe the actual runtime dependency or behavior. If validation passes but behavior is unchanged, identify the first ledger edge where the value disappears or changes. If only one backend works, inspect the backend selection table and every construction branch.

<a id="configuration-runtime-wiring-implement-a-complete-option"></a>

### Implement a complete option

Reuse the existing option and dependency injection conventions. Keep default application at the established boundary rather than independently defaulting in every wrapper. Preserve previously omitted-value behavior unless the public contract changes it.

When adding a selector, define supported values and required errors, route to the intended implementation, and pass backend-specific dependencies through the real constructor. Updating an enum/schema without constructor selection produces a feature that validates but does nothing.

When a signature changes, inspect callers, wrappers, mocks, generated adapters, interface assertions, and platform variants. Use the existing generator for generated code. Match APIs to the versions pinned by the repository; do not upgrade a dependency to avoid learning the installed API.

<a id="configuration-runtime-wiring-verify-public-input-to-behavior"></a>

### Verify public input to behavior

Test omitted/default/explicit alternative and required invalid inputs through the configuration loader or command. Observe the constructed runtime dependency or resulting behavior. Compile applicable interfaces and run representative backend/platform checks. Helper or schema tests are useful but do not prove end-to-end wiring. If values are transported correctly but the consuming algorithm is wrong, use the mechanism skill for that algorithm instead of adding more configuration plumbing.

<a id="configuration-runtime-wiring-mechanism-details"></a>

<a id="configuration-runtime-wiring-wiring-patterns-defaults-and-whole-path-checks"></a>

### Wiring patterns, defaults, and whole-path checks

<a id="configuration-runtime-wiring-details-configuration-ledger"></a>

<a id="configuration-runtime-wiring-configuration-ledger"></a>

#### Configuration ledger

| Layer | Observation |
|---|---|
| Public format/schema | Accepted name, type, optionality, selector values |
| Decoder | Runtime value and whether presence survives |
| Validation/defaults | Effective value and which rule produced it |
| Options/constructor | Actual argument or injected dependency |
| Runtime consumer | Observable behavior, backend call, or selected implementation |

The first edge that breaks expected propagation is a better edit target than every layer that mentions the field. Still verify downstream consumers and alternative paths after fixing it.

<a id="configuration-runtime-wiring-details-worked-case-a-explicit-false-versus-inherited-default"></a>

<a id="configuration-runtime-wiring-worked-case-a-explicit-false-versus-inherited-default"></a>

#### Worked case A: explicit false versus inherited default

A service enables an option by default but permits explicit false. Code such as “if not enabled, apply default true” collapses omission and false. Preserve presence until the designated defaulting layer, using the repository's optional type, pointer, presence flag, or configuration representation. Resolve omission to the default there and pass an ordinary effective value downstream.

Test omitted, explicit false, and explicit true through the real loader. A constructor test passing `false` directly bypasses a decoder/defaulting defect.

<a id="configuration-runtime-wiring-details-worked-case-b-pluggable-resolver-backend"></a>

<a id="configuration-runtime-wiring-worked-case-b-pluggable-resolver-backend"></a>

#### Worked case B: pluggable resolver backend

A schema permits backend A and backend B. Validation recognizes B, but a constructor always creates A or neglects B's required client. Use a backend selection table and a fake client with distinguishable behavior. Load a configuration selecting B and invoke the public operation; assert B's client is used and its result/error is mapped correctly.

Exercise omitted selector compatibility and unsupported values. Do not initialize every external service just to make the selector test pass; inject dependencies at the existing seam.

<a id="configuration-runtime-wiring-details-counterexample-constructor-owned-defaults"></a>

<a id="configuration-runtime-wiring-counterexample-constructor-owned-defaults"></a>

#### Counterexample: constructor-owned defaults

A library explicitly promises that every constructor accepts raw options and applies defaults itself. Moving all defaulting into one configuration loader can break direct constructor users. The correct location follows the public API and established architecture. Where multiple entry points exist, centralize semantics without removing their guarantees.

<a id="configuration-runtime-wiring-details-change-checklist"></a>

<a id="configuration-runtime-wiring-change-checklist"></a>

#### Change checklist

Search the option's names and the changed interface's implementations. Inspect examples and embedded schemas that production actually loads. A generated file and its source can diverge if only the output is edited. Check build tags/platform-specific constructors without forcing unsupported targets to run locally.

For Go interfaces, method sets and parameter/result types must match; similar names are insufficient. For a Go/CUE boundary, distinguish schema acceptance from decoding a value and from invoking the consuming Go code. Confirm the checkout's version and actual API before choosing a decoding helper.

<a id="configuration-runtime-wiring-details-evidence-of-completion"></a>

<a id="configuration-runtime-wiring-evidence-of-completion"></a>

#### Evidence of completion

The complete chain is observed: a public input selects the intended runtime behavior; omission preserves compatibility; explicit alternatives survive; invalid input has its required error. Passing a schema check or adding a field declaration alone establishes only one edge.

<a id="configuration-runtime-wiring-concrete-runtime-boundaries"></a>

### Concrete runtime boundaries

Read [schema and types](#domain-schema-and-types) for real-default validation, optional subsections, tagged coercion, embedded assets, and error contracts. Read [service wiring](#domain-service-wiring) for exporters, sampling, propagation, credentials/address selection, and shutdown. Read identity and credentials for static/dynamic provider modes.

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

<a id="domain-schema-and-types"></a>

<a id="domain-presence-schemas-coercion-and-representation-boundaries"></a>

### Presence, schemas, coercion, and representation boundaries

Use when a setting validates but changes meaning, a decoder loses a union/tag, or schema validation disagrees with real configuration. The workflow is authored synthesis; linked language/library facts must be matched to the checkout's version.

<a id="domain-schema-and-types-model-presence-before-choosing-defaults"></a>

<a id="domain-model-presence-before-choosing-defaults"></a>

#### Model presence before choosing defaults

An optional scalar has at least two independent attributes: present and value. `present=false` is not the same as `present=true, value=false`. A language's default zero cannot necessarily recover that distinction after decoding. Keep a pointer, option wrapper, presence map, or existing decoder metadata until the authorized defaulting boundary.

Distinguish absent subsection, explicit null, and an empty subsection object. A value struct may emit `{}` for an unused backend and accidentally activate schema constraints requiring its fields. A nullable representation can preserve absence, but changing to pointers also requires updating callers, default initialization, and serialization. Do not add nullability solely to make a simplified test pass.

A constructor may accept only resolved selectors and reject empty input while the loader owns a backward-compatible default. Putting that default in both places can conceal missing wiring and violate direct-constructor behavior. Test the public loader and the constructor contracts separately.

<a id="domain-schema-and-types-validate-the-actual-representation"></a>

<a id="domain-validate-the-actual-representation"></a>

#### Validate the actual representation

Trace the real default factory, environment/file merge, decode-hook chain, field-name mapping, duration/enum adaptation, and schema input. A handcrafted map that resembles configuration omits exactly the tricky fields most likely to fail. Use the actual production factory and transformations.

Durations may be native values internally and strings at a schema boundary. The conversion must happen at that boundary without accidentally destroying the internal type or changing field names. Reflection over struct tags must parse tag options such as omission separately from the field name. Do not turn an entire tag string into the schema key.

Write a representation ledger: internal type, decoded type, encoded form, schema type, default, and presence semantics. For unions, list every accepted branch and its discriminator. A single-item collection may have a canonical serialized shorthand while a multi-item value uses a structured form; that does not imply every neighboring field adopts the same union.

<a id="domain-schema-and-types-cue-validation-pipeline"></a>

<a id="domain-cue-validation-pipeline"></a>

#### CUE validation pipeline

CUE's Go API provides values, unification, validation, and decoding. Unification combines constraints; an incomplete value and an inconsistent value are different states. Consult the pinned API's concrete-validation and context requirements. Source: [CUE value API](https://pkg.go.dev/cuelang.org/go/cue), with [Go integration concepts](https://cuelang.org/docs/concept/how-cue-works-with-go/).

Concrete API stages to identify in a version-compatible implementation include `Context.CompileBytes` or `CompileString` for schema text, `encoding/yaml.Extract` for YAML syntax to a CUE AST, `Context.BuildFile` for the input value, `LookupPath`/`ParsePath` when selecting a named definition, `Value.Unify` for constraints, and `Validate` with the required concreteness options. `Decode` into Go is a different operation. Source: [CUE YAML API](https://pkg.go.dev/cuelang.org/go/encoding/yaml#Extract). These names illustrate API roles, not a target wrapper signature; verify availability in the checkout's version.

Go can embed schema assets using `//go:embed` with the embed package, into supported variable forms. The pattern is relative to the package and the asset must participate in the build. Source: [Go embed](https://pkg.go.dev/embed). Test the built artifact outside the source directory rather than relying on a coincidental local file.

At a repair boundary, distinguish YAML syntax errors, schema compilation errors, type/constraint conflicts, and unresolved required values. A schema asset can compile in source tests but disappear from a packaged binary if runtime loading assumes an external path. Verify embedding/loading through the actual build mechanism and run from a directory that does not accidentally supply the source asset.

The validation error is itself part of some public APIs. Returning the original error, wrapping it with context, and substituting a sentinel preserve different things. Follow the specified contract. Do not prepend helpful text when byte-exact error identity or text is required. At another boundary, context may be explicitly required; there is no universal rule to wrap or never wrap.

<a id="domain-schema-and-types-type-coercion-is-a-decision-table"></a>

<a id="domain-type-coercion-is-a-decision-table"></a>

#### Type coercion is a decision table

Build `source category × requested type × source format` before writing conditionals. Categories can include missing/null, scalar text, bytes, boolean, integral/fractional numbers, mappings, hashable/unhashable sequences, and framework-tagged values. The contract chooses conversions; the table reveals accidental language behavior.

In Python, boolean participates in numeric type relationships; set membership can raise on an unhashable input; bytes and text have different iteration and encoding semantics. Source for Python truth/type behavior: [built-in types](https://docs.python.org/3/library/stdtypes.html). Avoid allowing those implementation details to define the public conversion policy. Guard classification before operations that assume hashability or a string path. A floating-point string, non-finite number, or byte sequence may be an invalid integer even when a loose conversion route accepts part of it.

Separate classification, conversion, metadata transfer, and error mapping:

```text
classify the original value without unsafe operations
resolve the requested type and its public alias/error identity
if no conversion is required, preserve the original object when promised
otherwise convert using the framework's defined semantics
transfer permitted provenance to derived values/elements
apply source-format-specific finishing, such as permitted unquoting
map only expected conversion failures to the required public error
```

Do not catch arbitrary exceptions from unrelated code and pretend they are bad user input. Also prevent incidental exceptions such as an unhashable-membership error from escaping when the contract specifies a conversion error.

<a id="domain-schema-and-types-provenance-and-resource-creation"></a>

<a id="domain-provenance-and-resource-creation"></a>

#### Provenance and resource creation

A tagged string converted to a collection can require metadata on both the collection and its newly derived items. Equal values with different provenance need not be interchangeable. Conversely, a temporary directory is a newly acquired resource; copying every source tag to its generated path can misrepresent its origin. Discover the framework's actual metadata operations instead of reconstructing its internal wrapper classes.

Temporary-resource conversion also needs a lifecycle policy: resolved parent path, unique creation, actual existence, cleanup owner, and failure behavior. Returning a formatted path is not equivalent to creating the promised directory. Unknown requested types may preserve input under one API and fail under another; use the documented fallback, not an invented canonical type.

<a id="domain-schema-and-types-go-interfaces-and-versioned-apis"></a>

<a id="domain-go-interfaces-and-versioned-apis"></a>

#### Go interfaces and versioned APIs

Method sets and pointer/value receivers affect interface satisfaction. Parameter and result types must match the interface contract; similar method names are insufficient. Source: [Go specification](https://go.dev/ref/spec#Method_sets).

After changing a returned tuple or optional field, enumerate all consumers: ordinary, streaming, short-circuit, error, mocks, platform variants, and generated adapters. A local call compiling does not prove the interface is migrated. Use real dependency packages and their pinned APIs; fabricating local SDK-shaped types or replacing dependencies to match an assumption can produce an incompatible repair.

<a id="domain-schema-and-types-tests-with-independent-expectations"></a>

<a id="domain-tests-with-independent-expectations"></a>

#### Tests with independent expectations

Exercise the real default config through its production decoding and schema boundary; explicit zero/false; absent optional sections; all affected union branches; non-concrete and invalid documents; tagged inputs; unhashable inputs; unknown types; and a failing conversion's exact public error. Test metadata and presence as well as equality. Round-trip tests supplement independent fixtures because a reader and writer can share a lossy assumption.

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

<a id="domain-service-wiring"></a>

<a id="domain-service-construction-telemetry-endpoint-selection-and-shutdown"></a>

### Service construction, telemetry, endpoint selection, and shutdown

Use when configuration changes span a provider factory, global service setup, clients, or shutdown. The procedures are authored synthesis; use the actual dependency APIs and public contracts.

<a id="domain-service-wiring-factory-versus-loader-responsibilities"></a>

<a id="domain-factory-versus-loader-responsibilities"></a>

#### Factory versus loader responsibilities

Write separate contracts for the public loader and dependency factory. If the loader resolves an omitted selector, the factory can legitimately reject an empty selector. Putting the default inside the factory can hide a caller that failed to load configuration and can violate a direct-factory API. Track enabled/disabled separately from selected implementation and from transport propagation.

For each backend record inputs, injectable client, construction side effects, output capability, shutdown function, and errors. A factory that returns a working object but a nil cleanup callback can fail the service's shutdown path. An exporter package that registers itself at import time can conflict with later configured construction. Prefer the existing lifecycle boundary and test initialization more than once only under the documented reset/reuse contract.

Distinguish process-wide one-time initialization from configuration-specific state. A resettable test seam does not imply live production reconfiguration is supported. If singleton state is part of the API, scope tests so they do not accidentally succeed only because another test initialized the desired backend first.

<a id="domain-service-wiring-endpoint-parsing-and-dependency-options"></a>

<a id="domain-endpoint-parsing-and-dependency-options"></a>

#### Endpoint parsing and dependency options

A plain host/port and a URL with a scheme can be parsed differently by standard URL libraries. Derive accepted forms, transport security, path handling, and default ports from the client contract. Do not turn a supported scheme into an unsupported transport by blindly passing the full input to a host-only option.

Verify headers and backend-specific options reach the actual constructed client. A schema accepting an endpoint and headers does not establish that either is used. Use distinguishable fake clients or transport spies and assert the public operation, not only the config object's fields.

<a id="domain-service-wiring-trace-sampling-and-propagation"></a>

<a id="domain-trace-sampling-and-propagation"></a>

#### Trace sampling and propagation

Sampling decides recording/export behavior; propagation carries context across boundaries. OpenTelemetry describes injection and extraction through carriers to preserve causal context. Source: [context propagation](https://opentelemetry.io/docs/concepts/context-propagation/).

Therefore disabling an exporter is not automatically permission to discard inbound/outbound propagation. Determine where server/client setup installs the propagator and whether that is required independently of tracing export. Compose named propagators using real implementations, not a custom object that merely advertises the expected field names. Names with similar labels can represent different wire formats.

A ratio in an inclusive range includes both endpoints. Explicit zero must not be mistaken for omission. Understand whether the chosen sampler is root ratio-based, parent-based, or another policy; test the appropriate root and parent cases rather than claiming that every incoming trace obeys the same decision. Validate out-of-range values at the specified boundary and preserve required public errors.

Keep file, environment, schema, and runtime representations aligned. If a list is ordered, preserve that ordering through decode hooks and composition. A generated schema or example configuration must reflect the actual loader rather than an obsolete field list.

<a id="domain-service-wiring-endpoint-selection-is-a-policy-tree"></a>

<a id="domain-endpoint-selection-is-a-policy-tree"></a>

#### Endpoint selection is a policy tree

Distinguish direct local credentials, a remote-cluster route, and discovered service endpoints. Each branch can choose both an address source and a credential/CA source. Keep those paired; a reachable address with credentials for a different branch can fail or select the wrong identity.

Build a table before coding: requested target, known local material, remote/local state, discovery results, credentials requested, candidates produced, and required error. Check a missing target before requesting credentials if the contract requires that ordering. Preserve discovery order and duplicates according to the public API rather than silently sorting or collapsing them.

During dialing, keep candidate address separate from committed active address. Update active state only when a connection succeeds. Check empty candidates, an early failed candidate followed by success, and all failures. Propagate context cancellation and the documented retry policy; do not turn authentication or certificate errors into an unbounded network retry.

<a id="domain-service-wiring-whole-path-tests"></a>

<a id="domain-whole-path-tests"></a>

#### Whole-path tests

Load public input, construct the provider, perform an observable operation, and execute shutdown. Cover disabled state, each backend, omitted versus explicit selector, malformed endpoint, boundary ratios, propagation with exporting disabled, and the endpoint/credential branch table as applicable. Inspect error type/text at the boundary where it is promised. A factory test and a server wiring test catch different omissions.

## Optional deeper cookbook

The mechanism and required technical facts are in this file. For an unusual language/runtime failure, the optional cookbook offers additional recipes: [go](../../../.repair-memory/cookbooks/go.md), [typescript](../../../.repair-memory/cookbooks/typescript.md). Select a recipe by its trigger; do not load the whole memory layer by default.


<a id="integration-seams"></a>

## Respect the actual dependency and constructor seam

For a manager/client refactor, inspect how callers construct it, how it obtains its client, and which binding actually performs I/O. A fake at the provider definition may not intercept a consumer's imported alias or preconstructed client. Check a real public call with the intended fake and inspect the outgoing arguments/side effects; mocks that replace the whole manager can hide the integration failure. Preserve public constructor signatures and fixture shapes unless the task changes them. A connection refusal during a nominal unit test can indicate a missed injection seam, not unavailable fleet infrastructure.

Verify enum members and SDK symbols against the pinned dependency's source or type declarations. Similar spelling or a member from a newer release is not evidence. A missing property error alone does not prove that an enum member is absent: inspect whether the containing value is undefined too. Type checking helps when declarations are accurate; actual binding checks are still useful at dynamic boundaries.

<a id="runtime-defaults"></a>

## Separate lookup time and canonical defaults

A module-level value may be captured at import, but some libraries expose lazy proxies. Inspect the actual implementation instead of assuming every constant-looking access is static. When configuration must honor construction-time or call-time overrides, use the established lookup at that boundary and test an override after import without reloading the module. Re-import can repeat registration and conceal the real timing contract.

DefaultConfig, normal Load(empty input), schema defaults and serialized output should agree where the public contract says they represent the same defaults. Compare them at that boundary rather than hand-authoring parallel tables. Explicit false, empty, null and absent values may have different meanings; a fixture conflict is not permission to erase a required default.

<a id="go-decode-and-types"></a>

## Go decoding, pinned types and interface migration

For mapstructure or another decoder, check which conversions the installed version already supplies. A hook for a typed slice should be gated on both source and destination types and leave unrelated values unchanged. Determine the environment-string separator from the interface; whitespace, comma and JSON arrays are different formats. Exercise the actual loader with YAML/JSON/environment input, not only the hook in isolation.

<a id="go-typed-slice-hook"></a>

### Worked example: one string field becomes a named-string slice

This complete Go package targets `github.com/mitchellh/mapstructure v1.5.0` and its [type-based hook API](https://pkg.go.dev/github.com/mitchellh/mapstructure@v1.5.0#DecodeHookFuncType). It models an invented configuration contract: `channels` accepts a whitespace-separated string or an ordinary list, while `label` remains one string and `tags` remains a normal string slice. The hook changes only the exact `string → []Channel` pair. `strings.Fields` deliberately collapses whitespace and returns no fields for an empty string; choose a different parser if empty items, quoting or commas have meaning.

```go recipe=typed-slice-decode
package recipe

import (
    "reflect"
    "strings"

    "github.com/mitchellh/mapstructure"
)

type Channel string

type channelConfig struct {
    Channels []Channel `mapstructure:"channels"`
    Label    string    `mapstructure:"label"`
    Tags     []string  `mapstructure:"tags"`
}

func channelListHook() mapstructure.DecodeHookFuncType {
    source := reflect.TypeOf("")
    target := reflect.TypeOf([]Channel{})
    return func(from, to reflect.Type, data interface{}) (interface{}, error) {
        if from != source || to != target {
            return data, nil
        }
        words := strings.Fields(data.(string))
        result := make([]Channel, len(words))
        for i, word := range words {
            result[i] = Channel(word)
        }
        return result, nil
    }
}

func decodeChannels(input map[string]interface{}) (channelConfig, error) {
    var result channelConfig
    decoder, err := mapstructure.NewDecoder(&mapstructure.DecoderConfig{
        Result:  &result,
        TagName: "mapstructure",
        DecodeHook: mapstructure.ComposeDecodeHookFunc(channelListHook()),
    })
    if err != nil {
        return result, err
    }
    err = decoder.Decode(input)
    return result, err
}
```

For `{"channels": "alpha beta", "label": "keep spaces", "tags": []string{"x y"}}`, expect `[]Channel{"alpha", "beta"}`, the unchanged label, and one unsplit tag. Also check an ordinary `[]interface{}{"alpha", "beta"}` list, a named-string slice, empty text, and invalid numeric input. The hook leaves nonmatching types alone; the decoder still decides whether those values are valid. In a real loader, compose with its existing hooks in the established order instead of replacing them. Confirm the actual environment/config path reaches this decoder: a direct call to the example proves neither Viper environment binding nor the repository's loading behavior. Enum validation, if required, belongs at the specified boundary; this illustration does not invent allowed channel names.

### Package-qualified types and caller migration

Go types with similar printed names or fields are not automatically assignable. Inspect package-qualified definitions, aliases, pointer/value forms, method sets, mocks and generated adapters before reusing or introducing a type. For a signature or struct-field change, enumerate all callers including tests and platform files; a new return value needs a meaningful use or intentional discard at each consumer. Compile the affected test packages when possible as well as production code. Go formatting cannot detect assignment arity or interface conformance.

<a id="schema-presence"></a>

## Schema lookup, presence and decoded defaults

If a public schema consumer looks up a named definition, expose that exact name/path and test lookup before validation. In CUE, optional field presence (`field?`) does not by itself allow a null value. Permit null only when the decoded configuration contract admits it. Similarly, a disjunction of named strings does not accept an empty string unless explicitly included; decide whether the loader supplies a named default or preserves empty.

Validate the actual decoded map rather than a hand-written approximation. Closed schema definitions may reject fields emitted by embedded structs, pointers, tags or zero-value serialization. Enumerate the real fields and required optionality, then test empty/default input and representative populated input. Avoid blanket `| null` or arbitrary open fields as a way to silence schema errors. Those changes can discard a validation promise.

<a id="identity-and-propagation"></a>

## Preserve identity, provenance and argument positions

Treat an opaque path or entity ID as opaque: capture the full promised value and forward it unchanged unless normalization is explicitly part of the API. For discriminators stored alongside raw material, establish whether metadata is authoritative, inferred, stale or untrusted before choosing it over parsing. Avoid redundant reclassification when a trusted validated discriminator already supplies the answer; do not blindly trust arbitrary metadata.

Request-context users and persisted users have different provenance. Identify which fields are authoritative for the operation; a context object may already be validated, while persistent credential validation may require the current stored record. An extra database read is not automatically safer, and client-supplied fields are not automatically authoritative.

When adding an optional positional argument, preserve every downstream slot through wrappers. Omitting a trailing argument does NOT shift earlier arguments in JavaScript; however explicit `undefined` may be part of an observable call contract, and omitting an interior argument does shift later ones. Test absence and presence through each actual wrapper, and distinguish value semantics from `arguments.length` or mock call-tuple assertions when those are contractual.
