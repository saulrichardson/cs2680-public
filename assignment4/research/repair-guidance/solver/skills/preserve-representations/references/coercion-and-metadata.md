# Conversion, identity, and provenance

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

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

<a id="representation-consistency-details-worked-case-b-tagged-configuration-values"></a>

<a id="representation-consistency-worked-case-b-tagged-configuration-values"></a>

#### Worked case B: tagged configuration values

A configuration field accepts either a literal or a structured reference with a tag and parameters. Decoding directly into the literal type can erase the reference variant or silently yield a zero value. Preserve the discriminant long enough to select a valid branch, validate that branch's fields, and construct the public model using the project's schema rules.

Test each legal variant, a missing tag where the format requires one, an unknown tag, and omitted configuration. Do not turn an unsupported tag into the default variant unless explicitly required.

<a id="conversion-alias-identity"></a>

## Keep requested aliases and unchanged identity

Alias resolution may share conversion code without changing the public spelling used in an error. Retain the caller's original alias, with only normalization explicitly required by the interface, before mapping it to a canonical handler. Distinguish value equality from identity: returning a newly tagged/copied value can violate an unchanged-object promise even if `==` succeeds. Check expected conversion exceptions, exact alias-bearing errors and `result is input` when the API promises identity. Do not impose identity preservation when the contract intentionally creates a new value.
