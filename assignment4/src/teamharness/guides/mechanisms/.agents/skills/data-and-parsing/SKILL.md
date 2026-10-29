---
name: data-and-parsing
description: Parse and transform structured or encoded data while preserving meaning, linkage, provenance, identity, order, and serialization. Use for grammars, MIME/URLs, reader parity, coercion, record matching, or grouped output.
---

# Data, parsing, and representations

Follow the value from bytes or structured input into its public model. First decide what language/shape is accepted; next preserve presence and provenance; then interpret domain meaning, associate identities, and emit the required ordered representation. A formatting change cannot restore data a reader already discarded. A broad candidate query cannot establish identity. A round trip cannot establish correctness if both directions share the same loss.

Use the grammar chapter for acceptance; representation consistency for reader/writer divergence; linked records for associations; entity matching for existing-versus-new identity; and ordered collections for grouping and attached annotations. The technical chapters include the wire, MARC, schema, and type facts needed to apply those mechanisms. Examples choose explicit illustrative policies; the current public contract chooses the real one.

## Focused boundary checks

- [Compare the complete wire artifact](#complete-wire-body)
- [Keep requested aliases and unchanged identity](#conversion-alias-identity)
- [Choose ASCII or Unicode deliberately](#ascii-grammar-boundaries)
- [Trace adjacency in the chosen traversal direction](#ordered-merge-boundaries)
- [Migrate representations through producers and consumers](#shared-representations)
- [Propagate changed interfaces and accounting](#callers-and-accounting)
- [Know what an available check establishes](#fixture-and-check-scope)
- [Avoid mistaking an unobserved use for dead code](#source-cleanup-compatibility)
- [Parse linkage fields into typed keys](#linked-record-index)
- [Carry nullability across the whole representation](#nullable-migration)
- [Pair encoded reads with the correct writer mode](#encoded-read-write-pair)


## Chapter map

- [Grammar and normalization boundaries](#grammar-and-normalization)
- [Multiple representations and serialization](#representation-consistency)
- [Linked and repeated record alignment](#linked-record-alignment)
- [Entity matching and creation](#entity-resolution)
- [Ordered grouping, projection, and batches](#ordered-collections)
- [Wire formats: multipart bodies, encoded URLs, and structured responses](#domain-wire-formats)
- [MARC linkage, ordered records, and import identity](#domain-marc-and-imports)
- [Presence, schemas, coercion, and representation boundaries](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types)

<a id="grammar-and-normalization"></a>

## Grammar and normalization boundaries

<a id="grammar-and-normalization-write-the-accepted-language-before-editing-a-pattern"></a>

### Write the accepted language before editing a pattern

Collect valid, invalid, boundary, missing, and empty inputs from the public contract and existing fixtures. Record the complete expected output or error, not just “matches.” Distinguish structural syntax from payload whose case, punctuation, script, or encoding must survive.

Locate the public importer and its parser. A correct standalone helper is insufficient if the production path does not call it. Identify every representation or platform sharing the rule before tightening validation.

<a id="grammar-and-normalization-separate-recognition-from-transformation"></a>

### Separate recognition from transformation

Trace bytes → decoding → tokenization → validation → domain interpretation → output. Determine which layer owns each transformation. Decode once at the specified boundary. Case-fold syntax tokens only where the contract permits it; do not lowercase a whole value to simplify one comparison.

<a id="grammar-and-normalization-diagnose-with-discriminating-pairs"></a>

### Diagnose with discriminating pairs

Choose pairs differing in one property: missing/empty, case, unit symbol, separator, leading/trailing content, escaped/raw, Unicode/ASCII, or known/unknown variant. If malformed inputs pass, check full-input consumption and error handling. If valid inputs fail, find which constraint is stronger than the actual grammar.

<a id="grammar-and-normalization-implement-a-bounded-correction"></a>

### Implement a bounded correction

Prefer an existing parser/decoder over expanding a regex into an ad hoc nested grammar. Use a state machine or token parser when nesting, quoting, or escapes require it. Preserve original values separately when normalization is only for comparison. Reject ambiguous or unsupported forms only according to the documented policy.

Maintain public output shape and error behavior. Changes that turn malformed input into an empty successful value can hide the problem. Conversely, stricter validation must not reject supported legacy forms without a requirement to do so.

<a id="grammar-and-normalization-verify-the-entire-output"></a>

### Verify the entire output

Run through the real importer. Compare all meaningful fields, sequence order, duplicates, encoding, and required errors. Add boundary and round-trip cases only where those contracts apply. For units, validate semantic quantities as well as accepted spellings. For multiple readers, use the representation-consistency chapter so a correction in one format does not leave the other path inconsistent.

<a id="grammar-and-normalization-mechanism-details"></a>

<a id="grammar-and-normalization-acceptance-matrices-and-normalization-order"></a>

### Acceptance matrices and normalization order

<a id="grammar-and-normalization-details-acceptance-ledger"></a>

<a id="grammar-and-normalization-acceptance-ledger"></a>

#### Acceptance ledger

| Input class | Record |
|---|---|
| Valid canonical | Parsed value and complete public representation |
| Valid alternative | Which syntax differs and which semantic value is shared |
| Malformed | Required error or rejection behavior |
| Missing/empty | Whether they differ from each other and from an explicit default |
| Boundary | Minimum/maximum, delimiter positions, whitespace, full consumption |
| Encoded/Unicode | Which bytes or characters must be preserved |

Do not automatically take a Cartesian product of all dimensions. Select pairs that discriminate the proposed cause and cover promised forms.

<a id="grammar-and-normalization-details-worked-case-a-unit-bearing-measurements"></a>

<a id="grammar-and-normalization-worked-case-a-unit-bearing-measurements"></a>

#### Worked case A: unit-bearing measurements

A configuration grammar accepts a numeric value followed by a unit whose case can be meaningful. A global lowercase pass may turn distinct units into the same token. Parse the number and unit separately, validate the supported unit grammar, then convert with the semantic unit table. Preserve presentation separately if the public output requires it.

Test one pair whose symbols differ meaningfully, an unknown unit, a missing numeric part, and extra suffix content. Merely asserting that the regex matches both symbols does not establish correct conversion. Use actual unit definitions from the target domain; do not invent conversion factors.

<a id="grammar-and-normalization-details-worked-case-b-encoded-resource-identifiers"></a>

<a id="grammar-and-normalization-worked-case-b-encoded-resource-identifiers"></a>

#### Worked case B: encoded resource identifiers

A loader accepts a structured identifier containing escaped characters. Decoding before splitting can convert an escaped delimiter into a structural delimiter and change component boundaries. Splitting too early can be wrong under a different grammar. Locate the format's specified parsing order, preserve components through that boundary, and decode only where permitted.

Test a literal delimiter, its escaped form, a malformed escape, and an identifier whose payload differs only by case. Do not repeatedly decode until the string “looks normal.”

<a id="grammar-and-normalization-details-counterexample-intentional-canonicalization"></a>

<a id="grammar-and-normalization-counterexample-intentional-canonicalization"></a>

#### Counterexample: intentional canonicalization

A syntax keyword is explicitly case-insensitive. Preserving case for comparison may reject valid alternatives. The right policy is selective: canonicalize that keyword for recognition while preserving case-sensitive payload. “Never normalize” is as overbroad as “normalize everything.”

<a id="grammar-and-normalization-details-parser-outline"></a>

<a id="grammar-and-normalization-parser-outline"></a>

#### Parser outline

```text
read the original representation
decode the outer encoding at its defined boundary
recognize structure and preserve payload spans
validate the complete syntax and supported variants
interpret domain-specific tokens using their rules
construct the required public output without losing meaningful distinctions
```

Different formats can place these steps differently. Use the format's actual grammar and repository conventions, not this outline as a new specification.

<a id="grammar-and-normalization-details-common-false-positives"></a>

<a id="grammar-and-normalization-common-false-positives"></a>

#### Common false positives

A regex can find a valid substring inside invalid text. A test that checks only one parsed field can miss truncated payload or wrong units. A sanitizer can erase evidence of malformed input and accidentally produce a valid value. A helper can pass all fixtures while the public importer continues using its old path. Inspect full outputs and exercise the real consumer.

<a id="grammar-and-normalization-details-numeric-and-unit-grammars"></a>

<a id="grammar-and-normalization-numeric-and-unit-grammars"></a>

#### Numeric and unit grammars

Choose character classes deliberately. In many regex engines, a broad digit class accepts more than ASCII digits and a whitespace class accepts tabs/newlines and Unicode space. A contract allowing ASCII decimal text and literal spaces is narrower. Use full-input matching, specify whether leading whitespace/sign/exponent/fraction is accepted, and separate syntax failure from an unsupported unit.

Parse a complete unit token, not only its first letter. A recognized prefix followed by garbage can otherwise be accepted accidentally. Case may distinguish bits from bytes even when the size prefix itself has aliases. Convert numeric magnitude and unit semantics separately; avoid lowercasing the entire input. Binary versus decimal multipliers and rounding/overflow remain domain decisions.

Use discriminating pairs: a valid decimal versus trailing text, ASCII versus non-ASCII digits, a permitted space versus a different whitespace character, a supported unit versus a prefix plus junk, and a bit/byte case pair. Assert the required error category as well as accepted values. Do not adopt an exact grammar from this example when the current public contract accepts a broader language.

<a id="grammar-and-normalization-technical-parsing-references"></a>

### Technical parsing references

Read [wire formats](#domain-wire-formats) for multipart bytes and encoded-URL scanning; schema and types for coercion and provenance; or [platform inventory](../platforms-processes-and-resources/SKILL.md#platform-inventory) for source recognition and kernel/package semantics. Use the relevant grammar instead of enlarging one generic regex.

<a id="representation-consistency"></a>

## Multiple representations and serialization

<a id="representation-consistency-identify-semantic-equivalence"></a>

### Identify semantic equivalence

List accepted representations, their discriminants, and the shared public model. Determine which distinctions must survive: absent/null, integer/float, raw/decoded text, variant tags, field order, duplicates, and unknown fields as relevant. Representation equivalence is defined by the domain, not by identical source bytes.

Trace each reader into normalization and the real consumer. A helper used by one reader is not automatically shared by another. Inventory corresponding writers and schema/config encoders if the change affects round trips.

<a id="representation-consistency-find-the-divergence"></a>

### Find the divergence

Construct equivalent input pairs that enter different readers. Compare complete normalized output and public errors. If the readers agree but the final behavior differs, inspect later interpretation. If a union variant loses its tag or fields, find where decoding defaults to an overly narrow type or chooses a branch too early.

<a id="representation-consistency-repair-at-a-shared-semantic-boundary"></a>

### Repair at a shared semantic boundary

Share interpretation logic only where the formats promise the same semantics. Keep format-specific parsing and validation at their own boundaries. Do not force every variant through a lossy intermediate string or struct because it is convenient for one format.

Handle unsupported/malformed discriminants using the documented policy. Avoid accepting a partially decoded zero value as success after ignoring an error. Preserve omitted-value behavior and compatibility when expanding a public type or union.

For a round trip, choose the right equivalence relation: semantic model equality, canonical output equality, or byte preservation if explicitly required. Do not demand byte equality for a canonicalizing serializer or settle for semantic equality where opaque bytes must survive.

<a id="representation-consistency-verify-all-active-paths"></a>

### Verify all active paths

Test each supported reader with equivalent valid cases and format-specific malformed cases. Include every affected variant and a meaningful distinction such as absent versus explicit empty. Run through the public importer or API and at least one actual consumer. Check writers/round trips only where supported. For Go/CUE boundaries, consult the [schema and type chapter](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types) and the APIs pinned by the checkout rather than assuming the latest examples apply.

<a id="representation-consistency-mechanism-details"></a>

<a id="representation-consistency-representation-contracts-and-round-trip-examples"></a>

### Representation contracts and round-trip examples

<a id="representation-consistency-details-comparison-ledger"></a>

<a id="representation-consistency-comparison-ledger"></a>

#### Comparison ledger

For each input form record its parser, intermediate type, normalization, output model, consumer, and writer if one exists. Identify where the two paths should first become equivalent. Comparing only final display text can hide structural loss.

| Requirement | Appropriate assertion |
|---|---|
| Readers describe the same semantic model | Compare all meaningful model fields |
| Serializer emits canonical output | Compare canonical output, allowing specified normalization |
| Opaque payload is transported unchanged | Compare bytes at the relevant boundary |
| Unknown variants have a defined failure | Compare public error behavior, not an internal panic |
| Missing differs from explicit empty | Assert presence as well as value |

<a id="representation-consistency-details-worked-case-a-binary-and-text-inventory-files"></a>

<a id="representation-consistency-worked-case-a-binary-and-text-inventory-files"></a>

#### Worked case A: binary and text inventory files

An importer accepts binary and text versions of the same inventory. Both should produce the same ordered records, but only the text reader preserves an optional annotation. Follow the binary path's intermediate type: if the field is discarded there, a later shared formatter cannot restore it. Repair the appropriate reader/model boundary and feed both representations through the actual importer.

Add two records with distinct annotations, one absent value, and one malformed format-specific input. A test that hand-constructs the shared model bypasses the defect.

<a id="representation-consistency-details-worked-case-b-tagged-configuration-values"></a>

<a id="representation-consistency-worked-case-b-tagged-configuration-values"></a>

#### Worked case B: tagged configuration values

A configuration field accepts either a literal or a structured reference with a tag and parameters. Decoding directly into the literal type can erase the reference variant or silently yield a zero value. Preserve the discriminant long enough to select a valid branch, validate that branch's fields, and construct the public model using the project's schema rules.

Test each legal variant, a missing tag where the format requires one, an unknown tag, and omitted configuration. Do not turn an unsupported tag into the default variant unless explicitly required.

<a id="representation-consistency-details-counterexample-intentionally-different-formats"></a>

<a id="representation-consistency-counterexample-intentionally-different-formats"></a>

#### Counterexample: intentionally different formats

A compact export deliberately omits diagnostic metadata that a full export retains. Requiring the outputs to be identical expands the contract incorrectly. Compare the shared semantics and verify each format's additional guarantees separately.

<a id="representation-consistency-details-round-trip-outline"></a>

<a id="representation-consistency-round-trip-outline"></a>

#### Round-trip outline

```text
model A = decode(input)
output = encode(model A)
model B = decode(output)
assert equivalent(A, B) under the documented semantics
assert canonical output or byte preservation only if promised
```

Round-trip consistency alone is insufficient: a reader and writer can make the same wrong assumption and pass. Keep independent expected fixtures for required fields and representative variants.

<a id="representation-consistency-details-verification-traps"></a>

<a id="representation-consistency-verification-traps"></a>

#### Verification traps

Do not ignore decode errors, compare only record counts, drop unknown data without a policy, or let a generic JSON/object conversion silently change numeric or binary types. Use the repository's existing fixtures and actual encoding versions. If extending an interface or model, inspect alternate implementations and consumers so a locally correct decoder does not break another build path.

<a id="representation-consistency-schemas-protocols-and-readers"></a>

### Schemas, protocols, and readers

Read schema and types for union/default/coercion boundaries, wire formats for exact serialized bytes, or MARC readers for ordered repeated fields. Choose semantic equality versus byte compatibility explicitly.

<a id="linked-record-alignment"></a>

## Linked and repeated record alignment

<a id="linked-record-alignment-discover-the-association-rule"></a>

### Discover the association rule

Identify what links records: explicit identifier, occurrence number, source position, composite key, or a documented fallback. Record allowed cardinality, missing partners, duplicates, ordering, and output shape. Do not zip arrays merely because their lengths match.

Trace raw fields through parsing and intermediate normalization into the final consumer. Determine whether a map, set, sort, or fallback step loses multiplicity or source position before linking even begins.

<a id="linked-record-alignment-separate-pairing-from-formatting"></a>

### Separate pairing from formatting

Parse linkage into a structural identity while retaining raw values and source ordinal. Group or match by the defined identity. Apply missing/duplicate policies explicitly. Format linked outputs only after association is correct. For MARC alternate-script fields, read the [MARC chapter](#domain-marc-and-imports) and verify the version/format requirements.

<a id="linked-record-alignment-repair-without-inventing-symmetry"></a>

### Repair without inventing symmetry

A partner may be optional, a link may be one-to-many, or a record may intentionally be unlinked. Do not create a synthetic partner, discard an unpaired record, or duplicate a value to fill an array unless the public contract requires it.

Keep ordering information through dictionary/group operations and sort only by the contract's ordering rule. If output arrays are positional, append corresponding fields together at the established emission point; building each array independently can silently misalign them.

Preserve Unicode content and script-specific punctuation. Normalize link syntax only under the format's rules. A display normalization must not change the structural identity used to associate records.

<a id="linked-record-alignment-verify-relationships-not-presence-alone"></a>

### Verify relationships, not presence alone

Use multiple records with distinct values so wrong pairings are visible. Reorder source fields, remove one partner, repeat a link, and include an unlinked value as permitted. Compare complete structured output, including positions and cardinality. Check both direct parsing and real import paths. If the same association passes in one serialization and fails in another, inspect the representation readers too.

<a id="linked-record-alignment-mechanism-details"></a>

<a id="linked-record-alignment-association-models-and-worked-examples"></a>

### Association models and worked examples

<a id="linked-record-alignment-details-choose-identity-from-the-data-contract"></a>

<a id="linked-record-alignment-choose-identity-from-the-data-contract"></a>

#### Choose identity from the data contract

| Contract feature | Implication |
|---|---|
| Explicit relationship ID | Use that ID, including any required namespace or occurrence |
| Source order has meaning | Retain ordinals across grouping and normalization |
| Repeated values are legal | A map from identity to one value may lose data |
| Missing partner is legal | Absence needs an explicit output policy |
| Several representations describe one entity | Association and display selection are separate decisions |

Equal text is generally weaker evidence than explicit identity. Two records can have the same title or translation but belong to different entities.

<a id="linked-record-alignment-details-worked-case-a-multilingual-product-descriptions"></a>

<a id="linked-record-alignment-worked-case-a-multilingual-product-descriptions"></a>

#### Worked case A: multilingual product descriptions

Two products each have a primary description and a translated description, linked by product identity. The source interleaves records by language rather than product. Zipping “all primary values” with “all translated values” works only by accident when both lists use the same order. Index by the defined product identity while retaining product order and all legal language variants.

If one translation is missing, the next product's translation must not shift into its position. Assert the complete product-to-description structure, not just that all strings appear somewhere.

<a id="linked-record-alignment-details-worked-case-b-shipment-events-and-attachments"></a>

<a id="linked-record-alignment-worked-case-b-shipment-events-and-attachments"></a>

#### Worked case B: shipment events and attachments

Events link to zero or more attachments through event IDs. Attachments arrive before some events and may repeat. Parse all records with source ordinals, build the permitted association, and emit using the required event order. Decide whether duplicate attachments are meaningful from the contract; deduplicating by filename can erase distinct content.

An orphan attachment must follow the specified ignore/error/retain policy. Do not associate it with the nearest event merely to eliminate a gap.

<a id="linked-record-alignment-details-counterexample-documented-positional-pairing"></a>

<a id="linked-record-alignment-counterexample-documented-positional-pairing"></a>

#### Counterexample: documented positional pairing

A format explicitly defines parallel arrays by position and allows repeated identifiers. Replacing positional alignment with an identity map may merge legal entries. Identity-based matching is correct only when the contract defines that identity. Preserve positions when positions are the association rule.

<a id="linked-record-alignment-details-structural-outline"></a>

<a id="linked-record-alignment-structural-outline"></a>

#### Structural outline

```text
parse records while keeping raw value and source ordinal
derive a linkage identity using the format's actual grammar
associate records under its cardinality and missing/duplicate rules
emit complete related values together in the required order
preserve unpaired values according to the public policy
```

Keep linkage parsing separate from output transformations so a punctuation or Unicode cleanup cannot change associations accidentally.

<a id="linked-record-alignment-details-verification"></a>

<a id="linked-record-alignment-verification"></a>

#### Verification

Start with two distinguishable pairs, then reverse source order, omit a partner, add a legal repeat, and add a permitted orphan. Include different scripts if the format supports them. Assert which value belongs to which record, the number of outputs, their sequence, and all relevant fields. Exercise each reader/importer used by production; a test of a hand-built intermediate object can miss the loss of link metadata during decoding.

<a id="linked-record-alignment-marc-and-candidate-identity"></a>

### MARC and candidate identity

Read MARC and import records for ordered subfields, linkage grammar, reader parity, names, and downstream import integration. Use [entity resolution](#entity-resolution) for candidate selection; a correctly linked alternate field does not itself identify an existing database entity.

<a id="entity-resolution"></a>

## Entity matching and creation

Build a decision table from the public matching policy: available input evidence, candidate query, acceptance rule, priority, tie-breaker, and no-match behavior. Do not invent a confidence threshold or assume every normalization is allowed. For import-specific rules, see [record import semantics](#domain-marc-and-imports).

Separate candidate discovery from acceptance. A broad name/surname query can retrieve several candidates without making them interchangeable. Known conflicting dates, alternate names, missing dates, and different entity types require explicit treatment. Preserve original fields when returning a creation candidate; a failed match must not discard useful imported evidence.

Match the database's query semantics in tests. Case-insensitive full-string matching is different from substring search. Wildcards can have special meaning and escaping rules. A numeric identity tie-breaker is different from lexical ordering of its printed form. Follow the actual public rule and make it visible.

Inspect the production import caller to confirm that the result controls linking versus creation. Do not stop when a helper returns the right object if the caller ignores the decision, reorders candidates, or repeats a weaker lookup. Preserve any required return shape such as a list of candidates versus one chosen entity.

Test exact/alternate names, different case, wildcard boundaries, same name with compatible/conflicting/missing dates, multiple acceptable candidates, and no match. Assert the selected identity or creation fields, not just that the result is nonempty. Distinguish a query failure from a valid no-match result according to the API.

<a id="entity-resolution-mechanism-details"></a>

<a id="entity-resolution-candidate-retrieval-identity-evidence-and-deterministic-choice"></a>

### Candidate retrieval, identity evidence, and deterministic choice

<a id="entity-resolution-details-decision-stages"></a>

<a id="entity-resolution-decision-stages"></a>

#### Decision stages

1. Parse input evidence while preserving absence and original spelling.
2. Generate candidates using the permitted queries.
3. Evaluate the required evidence for that query path.
4. Apply priority and deterministic tie-breaking.
5. Return the required existing identity or complete creation candidate.

These stages can share code, but mixing them makes a broad retrieval rule look like an identity rule. For example, retrieving by surname is not evidence that all retrieved people share identity.

<a id="entity-resolution-details-missing-versus-conflicting-evidence"></a>

<a id="entity-resolution-missing-versus-conflicting-evidence"></a>

#### Missing versus conflicting evidence

An absent date does not establish agreement, and a known conflicting year can invalidate an otherwise close name. Some APIs intentionally permit name-only fallback; others require enough dates to use alternate-name/surname paths. Write each path's conditions separately. Do not make an incomplete-input fallback accidentally override a stricter complete-input rule.

If a contract compares only a year component, preserve full input separately and compare at that granularity. Parsing a year should not silently invent one from arbitrary text. Likewise, normalizing case for matching must not destroy the spelling retained in a new entity's display name.

<a id="entity-resolution-details-worked-example"></a>

<a id="entity-resolution-worked-example"></a>

#### Worked example

An invented import for “Morgan Vale” has an alternate name and known life dates. Candidate A matches the display name but conflicts in one required date. Candidate B matches the permitted alternate name and compatible dates. Candidate C matches only a surname and has incomplete metadata. The policy determines which paths are eligible and their priority. A generic “first text match” rule cannot represent these distinctions.

Now remove the input dates. That is a different policy row, not automatic permission to reuse the decision from the complete-evidence case. Return a new candidate with its original fields intact when no allowed path accepts an existing identity.

<a id="entity-resolution-details-query-fidelity-and-ordering"></a>

<a id="entity-resolution-query-fidelity-and-ordering"></a>

#### Query fidelity and ordering

A fake query evaluator should reproduce the production operator's full-string, case, wildcard, and escaping behavior used by this code. A test double implemented as `pattern in name` may accept a candidate the database would reject. Keep query emulation scoped; use an integration fixture where available rather than replacing the database's full query language.

When a tie-breaker is defined over numeric IDs, sort numeric values rather than strings. When source order defines priority, avoid a set/dictionary transformation that changes it. Test at least two valid candidates and deliberately choose identities whose numeric and lexical orders differ if that distinction is relevant.

<a id="entity-resolution-details-counterexample-and-checks"></a>

<a id="entity-resolution-counterexample-and-checks"></a>

#### Counterexample and checks

Two different people can share a normalized name and even a partial date. Do not generalize this procedure into a universal fuzzy identity merge. If policy leaves ambiguity unresolved, preserve that uncertainty or return candidates as specified. Test the actual caller's selected ID and whether a creation operation occurs, including the no-match and query-error branches.

<a id="ordered-collections"></a>

## Ordered grouping, projection, and batches

Discover the input order, record identity, grouping relation, multiplicity, attached annotations, and output/execution contract. Overlap, equal labels, equal IDs, and adjacency are different relations.

For adjacent context windows, determine the allowed overlap rule and whether every member must have a direct match. Merge only eligible neighbors; preserve annotations by mapping indices or stable identities into the new sequence. Do not globally deduplicate repeated events that are meaningful outside the overlap relation. Ensure consumed windows are not rendered again.

For catalog projection, keep selection membership separate from catalog ordering and label lookup. Define unknown-ID and omitted-value behavior. For asynchronous updates, collect explicit per-record results and combine them under a deterministic order, then execute at the documented commit boundary.

Use independent default collections for independent batches. Preserve adds, deletes, commit options, and execution return values when callers inspect them. A representation change is incomplete if a previous caller still executes each update separately or ignores the new result.

Test overlap chains, an intervening gap, annotation remapping, duplicates outside the allowed overlap, shuffled callback completion, unknown catalog IDs, and two independent batches. Assert the complete ordered structure, highlight/selection identity, exact combined operation, and public result. A set of expected values cannot detect wrong order or attachment.

<a id="ordered-collections-mechanism-details"></a>

<a id="ordered-collections-grouping-with-attached-metadata"></a>

### Grouping with attached metadata

<a id="ordered-collections-details-overlapping-ordered-windows"></a>

<a id="ordered-collections-overlapping-ordered-windows"></a>

#### Overlapping ordered windows

Represent each window as an ordered sequence plus annotations referring to its elements. Determine overlap k from the application's relation; it can be one boundary element, a permitted suffix/prefix, an interval relation, or none. Do not replace a narrow relation with a general longest-overlap algorithm without a requirement.

If a sequence of length L appends a new window after skipping k overlapping elements, a new-window index j maps to `L-k+j`, assuming the skipped elements correspond to the existing suffix. This formula applies to annotations too, not only visible elements. Retain annotations on overlapping elements; skipping their bytes does not mean dropping their direct-match status.

An invented example merges `[a,b,c,d]` with `[c,d,e]` using k=2. The new sequence is `[a,b,c,d,e]`; a mark on new-window index 2 maps to output index 4. A mark on its index 0 belongs to existing output index 2. Compare this mapping with a common error that adds L without accounting for skipped overlap.

```text
start a group from the first eligible window
for each next window in source order:
    if the current group and next window satisfy the defined relation:
        compute their allowed overlap
        remap the next annotations into the group
        append only its non-overlapping suffix
    else:
        emit the completed group once
        start a new group
emit the final group
```

Do not render an intermediate group while it may still absorb a neighbor unless the rendering architecture deliberately updates that same group. Keep interaction/permalink targets attached to their original element identities, not a representative window's first match.

<a id="ordered-collections-details-catalog-projection"></a>

<a id="ordered-collections-catalog-projection"></a>

#### Catalog projection

When catalog order is authoritative, iterate it and test selection membership while looking up labels/values. If user order is authoritative, use that instead. Preserve duplicates only when the contract permits them. A lookup miss must follow the specified reject/omit/retain behavior rather than inserting a label from a previous item.

<a id="ordered-collections-details-async-updates-and-inspectable-commands"></a>

<a id="ordered-collections-async-updates-and-inspectable-commands"></a>

#### Async updates and inspectable commands

Parallel work produces results in completion order, which may differ from required command order. Associate each result with its source position/record type. Aggregate explicit adds/deletes/options only after the necessary work settles. Define failure policy before deciding whether partial updates may execute.

Construct per-instance mutable lists/maps rather than shared defaults. Test a second empty batch after mutating the first; it must remain independent. Keep command composition separate from execution so it can be inspected and the actual executor return value propagated.

<a id="ordered-collections-details-verification-and-counterexample"></a>

<a id="ordered-collections-verification-and-counterexample"></a>

#### Verification and counterexample

Check a multi-window chain, a non-overlapping gap, all relevant annotation positions, empty input, and the final flush. Use distinct IDs and marks so shifts are visible. Reverse asynchronous completion order, inspect exact batch contents/commit behavior, and exercise execution failure.

A transaction log in which every repeated ID represents a new event should not be globally deduplicated. A source order defined by a protocol must not be sorted just because sets make equality tests easy. State the relation first; choose the collection algorithm second.

<a id="domain-wire-formats"></a>

<a id="domain-wire-formats-multipart-bodies-encoded-urls-and-structured-responses"></a>

### Wire formats: multipart bodies, encoded URLs, and structured responses

Read this before changing byte-level serializers or scanners. Protocol facts below come from the linked standards; the repair procedures and examples are authored reasoning. The application's current public contract chooses ordering, error behavior, supported inputs, and compatibility.

<a id="domain-wire-formats-multipart-structure-and-representation"></a>

<a id="domain-multipart-structure-and-representation"></a>

#### Multipart structure and representation

RFC 7578 defines form-data parts with a Content-Disposition containing a field name; file parts may include a filename. RFC 2046 supplies multipart boundary/delimiter framing. A boundary parameter and the delimiter bytes in the body must agree; the closing delimiter differs from an ordinary separator. Headers and bodies are separated by a blank line. MIME base64 output is limited to 76 encoded characters per line under RFC 2045; that is a protocol rule, not an arbitrary formatter width. Modern HTTP form-data discourages Content-Transfer-Encoding, while a legacy application can require a MIME serialization for compatibility. Preserve the current contract explicitly rather than silently choosing either policy. Sources: [RFC 7578](https://www.rfc-editor.org/rfc/rfc7578), [RFC 2046](https://www.rfc-editor.org/rfc/rfc2046), [RFC 2045](https://www.rfc-editor.org/rfc/rfc2045).

Build a semantic part model before rendering bytes: field name, optional display filename, payload bytes, media type, and transfer-encoding policy. Distinguish a plain text field from a file descriptor whose payload happens to be text. Distinguish absent content from an explicitly supplied empty payload. A truthiness check can accidentally read a file instead of sending the caller's empty content.

Resolve payload independently of metadata. The path used to read bytes and the filename sent to a receiver need not be the same. Do not expose a local directory path as a display filename by accident. Determine whether inline content, a referenced file, or both are allowed, and which source has precedence. Decide MIME type from explicit metadata, permitted inference, and the contract's fallback; a guesser that returns no type and one that raises are different failures to handle deliberately.

<a id="domain-wire-formats-library-serializers-and-exact-compatibility"></a>

<a id="domain-library-serializers-and-exact-compatibility"></a>

##### Library serializers and exact compatibility

Python's email package can add MIME headers, charset parameters, encodings, and policy-dependent line endings. Its policies control serialization behavior; MIME subclasses perform additional construction. Sources: [email policies](https://docs.python.org/3/library/email.policy.html), [MIME classes](https://docs.python.org/3/library/email.mime.html).

Consequently, calling a convenient text helper does not establish exact wire compatibility. Inspect the bytes it emits, including implicit MIME-Version or charset additions, header order, transfer encoding, CRLF versus LF, and final newline. If a fixture defines exact serialization, compare against that fixture after substituting only legitimately variable data such as a generated boundary. Do not normalize all whitespace or header order in the assertion when those are requirements.

```text
validate outer input and each part descriptor
resolve the payload source using presence, not truthiness
convert to bytes once at the documented encoding boundary
choose media type and transfer policy independently
construct allowed headers and serialize with the required policy
return the content type and the body generated with the same boundary
```

The algorithm does not prescribe field sorting, header order, a boundary spelling, or one universal transfer policy. Learn those from the current interface. Avoid flattening all errors into one exception if the API distinguishes bad types, missing content, unreadable files, and serializer failure.

<a id="domain-wire-formats-worked-checks"></a>

<a id="domain-worked-checks"></a>

##### Worked checks

An invented upload contains a Unicode text label, an empty inline binary field, and a disk-backed image whose display name excludes its directory. Parse the result with an independent MIME parser and assert names, payload bytes, filenames, media types, and number of parts. Also compare exact serialized bytes where compatibility requires it. Include bytes containing line breaks and boundary-like text. The parser check catches malformed framing; the byte check catches unwanted library normalization. Neither replaces the other.

<a id="domain-wire-formats-encoded-url-editing"></a>

<a id="domain-encoded-url-editing"></a>

#### Encoded URL editing

RFC 3986 gives percent-encoding the form `%` followed by two hexadecimal digits. Reserved delimiters can change component structure when decoded. Source: [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986).

Represent a URL as components plus the encoded spelling required by the operation. Select the component or segment before looking for editable numbers. Preserve unrelated components and encoded octets. A regex over the whole displayed URL can change a port, query parameter, fragment, or the hex digits of an escape instead of the requested resource number.

An encoded scanner can advance over each valid `%HH` triplet as an indivisible token and recognize permitted digit runs outside those tokens. Whether a malformed percent sequence is rejected, preserved, or delegated to the URL parser is a contract decision. Do not repeatedly decode to make scanning easy: `%25` followed by digits is not permission to reinterpret the result as another escape.

For a chosen numeric span, separate numeric arithmetic from rendering. Discover signed/unsigned behavior, overflow/underflow policy, leading-zero width, and what happens when no eligible number exists. Replace only the chosen span, then serialize through an API/mode that preserves the encoded representation. An otherwise correct edit can be undone by a serializer that decodes or canonicalizes unrelated text.

For example, `/archive/0042/part%2Fblue?view=8#chapter3` has several digit sequences with different roles. The caller's operation determines the eligible component. A test should assert preservation of the escape and all unrelated components, not merely that some number increased.

<a id="domain-wire-formats-structured-service-responses"></a>

<a id="domain-structured-service-responses"></a>

#### Structured service responses

Follow request format, response decoding, document extraction, and error mapping as one path. A caller that switches a query parameter to request JSON must also use the JSON response envelope and preserve its public result shape. Distinguish malformed serialization, a valid service-error object, missing required fields, and an empty successful result. Avoid an overly broad catch that converts all four to “no results.”

Use independent fixtures for an ordinary result, an empty result, a malformed body, and a service error. Inspect the request sent to the fake service and the returned public model. Regex extraction from structured data can pass a simple fixture while losing nested values or escaped strings.

<a id="domain-marc-and-imports"></a>

<a id="domain-marc-linkage-ordered-records-and-import-identity"></a>

### MARC linkage, ordered records, and import identity

Use for bibliographic field adapters and, by analogy, any repeated linked-record import. Format facts are scoped to MARC 21. Normalization, matching, output shape, and error policy remain application contracts.

<a id="domain-marc-and-imports-keep-a-lossless-field-view"></a>

<a id="domain-keep-a-lossless-field-view"></a>

#### Keep a lossless field view

A useful intermediate field retains its tag, indicators, source ordinal, and an ordered sequence of `(subfield_code, value)` pairs. A dictionary from code to one value cannot represent repeated subfields or their interleaving. A dictionary from code to a list can retain multiplicity while still losing cross-code order. Provide an ordered iterator and make first-value or all-values convenience operations explicit.

Binary and XML readers should adapt into the same field-access contract while preserving their input-specific decoding. Check what their libraries actually return: a flattened sequence, code/value objects, repeated XML elements, or another form. Do not assume that testing a hand-built intermediate field exercises both adapters.

<a id="domain-marc-and-imports-parse-linkage-structurally"></a>

<a id="domain-parse-linkage-structurally"></a>

#### Parse linkage structurally

MARC 880 carries an alternate graphic representation. Its association uses `$6`, whose structure includes a linking tag and occurrence number, with optional script/orientation information. The regular field's linkage points to 880; the alternate field points to the associated regular tag. Occurrence `00` has special unlinked meaning. Sources: [MARC 880](https://www.loc.gov/marc/bibliographic/bd880.html), [control subfields](https://www.loc.gov/marc/bibliographic/ecbdcntf.html).

The structural shape is `[linking tag]-[two-digit occurrence]/[script code]/[orientation code]`, with suffixes governed by the format. An invented ordinary added-name field can point to `880-17`, while the alternate points back to `700-17/(N`; the suffix is not part of the occurrence. The number matches a set within the record and does not define its display order.

Match the relevant tag and occurrence, not just the number or field position. Keep script/orientation suffixes separate from the structural association. Occurrence numbers are not record IDs or array offsets. An unlinked alternate field should follow an explicit policy rather than accidentally pairing with whichever ordinary field appears first.

```text
adapt each raw field into an ordered view
parse linkage without changing the payload text
build an index that can retain multiple legitimate candidates
for each ordinary field:
    select alternates matching its defined tag/occurrence relation
    apply the same semantic extractor to primary and alternate fields
emit unlinked alternates according to the import contract
```

This is an association model, not a prescription for which tags an application's author/title importer must include. Read that choice from the public issue and existing code. A helper that silently defaults to one tag may work for the first author and fail for repeated added entries; carry the calling field's identity where needed.

<a id="domain-marc-and-imports-normalization-with-semantic-boundaries"></a>

<a id="domain-normalization-with-semantic-boundaries"></a>

#### Normalization with semantic boundaries

Name construction can combine several subfields whose order and punctuation differ in meaning. Separate joining, whitespace trimming, separator removal, and trailing-period handling. Periods can represent initials or occur after a single-character name; blanket stripping can erase intended punctuation. Reuse a verified application policy for primary and alternate representations rather than inventing a second almost-equal normalizer.

Similarly, parse birth/death information separately from display names. Preserve an absent date instead of manufacturing a zero year. Do not apply person-specific parsing to organizations or events unless the contract extends it. Keep alternate names as distinct evidence, with stable deduplication if the application calls for it; deduplicating by an aggressively normalized key can merge different script forms.

<a id="domain-marc-and-imports-independent-alignment-example"></a>

<a id="domain-independent-alignment-example"></a>

##### Independent alignment example

An invented record contains ordinary fields A and B, alternate representations for B before A, a repeated subfield, and an unlinked alternate. The correct associations follow linkage, not order. Removing A's alternate must not shift B's into A. A complete expected result should establish every pairing, retained value, order, and missing/unlinked outcome. Supply equivalent binary and XML fixtures where both readers are supported.

<a id="domain-marc-and-imports-entity-matching-is-evidence-aggregation"></a>

<a id="domain-entity-matching-is-evidence-aggregation"></a>

#### Entity matching is evidence aggregation

Candidate retrieval and identity confirmation are different steps. Normalized names, alternate names, surname variants, and dates can help retrieve candidates. A similar name by itself can be ambiguous. Treat a known incompatible date as evidence against a match; an absent date is uncertainty, not positive agreement. Record the policy for resolving multiple viable candidates and when a new entity should be created.

For an invented pair of authors named “R. Vale,” one candidate has a conflicting known birth year and another has a matching alternate name but no dates. The application must decide how to rank and whether that is sufficient confidence. The guide does not choose a universal threshold. Test the public import caller so the candidate decision actually controls linking or creation; a standalone matcher can return the right object while the importer ignores it.

<a id="domain-marc-and-imports-other-imported-fields"></a>

<a id="domain-other-imported-fields"></a>

#### Other imported fields

Language codes, page counts, identifiers, and observations need explicit missing/invalid policies. Distinguish parsing a meaningful scalar from extracting a number that happened to occur in descriptive text. Preserve canonical catalog order independently of label lookup or user input order. When a consumer receives an ID list, use the catalog as the source of labels, handle unknown IDs as specified, and verify the persisted/public representation.

Do not treat a newly written conversion helper as an integrated import. Trace it from the production importer to the returned record and persistence. If a field changes type or optionality, check existing records and alternate readers too.

<a id="domain-marc-and-imports-verification-that-rules-out-plausible-wrong-fixes"></a>

<a id="domain-verification-that-rules-out-plausible-wrong-fixes"></a>

#### Verification that rules out plausible wrong fixes

Use distinguishable values for several primary/alternate pairs; shuffled source order; repeated subfields interleaved across codes; missing links; unlinked alternates; Unicode; initials/punctuation; and absent/conflicting dates. Choose only applicable cases. Compare the full structured output and the import caller's selected entity. A count of names or a substring assertion cannot detect the wrong relationships.

<a id="domain-marc-and-imports-validity-selection-and-punctuation-scope"></a>

<a id="domain-validity-selection-and-punctuation-scope"></a>

#### Validity selection and punctuation scope

Repeated control fields can differ in completeness or validity under the format. Define which candidate is usable, then use the public tie-breaker among usable candidates; blindly taking the first occurrence can select malformed data. Keep this policy separate from ordinary repeated-field access.

Do not use one strip-character set for every output field. Names, series, subtitles, publisher abbreviations, and bracketed annotations can have different conventions. Trimming punctuation can remove a meaningful closing bracket or initial marker. Test balanced punctuation, optional subtitle/fallback source selection, stable deduplication, and the exact field-level policy through both readers rather than applying a record-wide cleanup pass.

<a id="domain-schema-and-types"></a>

<a id="domain-presence-schemas-coercion-and-representation-boundaries"></a>

### Presence, schemas, coercion, and representation boundaries

The authoritative chapter is [in the configuration skill](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types). It covers presence, decoder/schema boundaries, coercion, provenance and pinned interfaces. Read its relevant body, not only the headings below. These redirect anchors preserve existing links without maintaining a second copy.

<a id="domain-schema-and-types-model-presence-before-choosing-defaults"></a>

<a id="domain-model-presence-before-choosing-defaults"></a>

#### Model presence before choosing defaults

Read [model presence before choosing defaults](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-model-presence-before-choosing-defaults) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-validate-the-actual-representation"></a>

<a id="domain-validate-the-actual-representation"></a>

#### Validate the actual representation

Read [validate the actual representation](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-validate-the-actual-representation) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-cue-validation-pipeline"></a>

<a id="domain-cue-validation-pipeline"></a>

#### CUE validation pipeline

Read [cue validation pipeline](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-cue-validation-pipeline) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-type-coercion-is-a-decision-table"></a>

<a id="domain-type-coercion-is-a-decision-table"></a>

#### Type coercion is a decision table

Read [type coercion is a decision table](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-type-coercion-is-a-decision-table) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-provenance-and-resource-creation"></a>

<a id="domain-provenance-and-resource-creation"></a>

#### Provenance and resource creation

Read [provenance and resource creation](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-provenance-and-resource-creation) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-go-interfaces-and-versioned-apis"></a>

<a id="domain-go-interfaces-and-versioned-apis"></a>

#### Go interfaces and versioned APIs

Read [go interfaces and versioned apis](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-go-interfaces-and-versioned-apis) in the authoritative schema/type chapter.

<a id="domain-schema-and-types-tests-with-independent-expectations"></a>

<a id="domain-tests-with-independent-expectations"></a>

#### Tests with independent expectations

Read [tests with independent expectations](../configuration-interfaces-and-identity/SKILL.md#domain-schema-and-types-tests-with-independent-expectations) in the authoritative schema/type chapter.

## Optional deeper cookbook

The mechanism guidance is in this file and its linked schema/type chapter. For an unusual language/runtime failure, the optional cookbook offers additional recipes: [python](../../../.repair-memory/cookbooks/python.md), [go](../../../.repair-memory/cookbooks/go.md). Select a recipe by its trigger; do not load the whole memory layer by default.


<a id="complete-wire-body"></a>

## Compare the complete wire artifact

A MIME message and its body are different artifacts. A library serializer may emit top-level MIME headers plus a blank line before the first multipart boundary, while an HTTP body contract expects the boundary at byte zero. Determine which representation the public interface returns before choosing an extraction method. Do not make a body-only check skip an unexpected header merely because the implementation emitted it.

Use a parser, documented serializer option, or verified header/body separator for extraction; blindly searching for a boundary substring can collide with content or mishandle preambles. Check the entire returned bytes with an independent expected fixture: first boundary, line endings, header ordering/policy, each part's payload and transfer encoding, closing delimiter, and final newline. A check derived from the same serializer can reproduce the same defect. For other formats, inspect the actual consumer-visible MIME/type, decoded object and bytes through the whole pipeline, rather than concluding format preservation from an encoder branch.

<a id="conversion-alias-identity"></a>

## Keep requested aliases and unchanged identity

Alias resolution may share conversion code without changing the public spelling used in an error. Retain the caller's original alias, with only normalization explicitly required by the interface, before mapping it to a canonical handler. Distinguish value equality from identity: returning a newly tagged/copied value can violate an unchanged-object promise even if `==` succeeds. Check expected conversion exceptions, exact alias-bearing errors and `result is input` when the API promises identity. Do not impose identity preservation when the contract intentionally creates a new value.

<a id="ascii-grammar-boundaries"></a>

## Choose ASCII or Unicode deliberately

In Python string regexes, `\d` includes Unicode decimal digits and `\s` includes Unicode whitespace; neither means "all possible digit-like/space-like characters." In particular, do not assume every zero-width character matches `\s`. If the grammar requires ASCII digits and literal spaces, use `[0-9]` and `[ ]` (or a deliberate ASCII mode), and consume the whole input with `fullmatch`. Decide independently whether leading/trailing space is allowed. Check ASCII digits, an Arabic-Indic decimal digit, non-breaking space, tab, newline, and a valid prefix followed by junk. Broad `.strip()` normalization can erase exactly the evidence a strict grammar must reject.

<a id="ordered-merge-boundaries"></a>

## Trace adjacency in the chosen traversal direction

For an ordered merge, write down which end of each item meets its neighbor in the chosen traversal. Forward iteration naturally compares the previous result with the next input; reverse traversal needs an explicit reversal of that relation. Retain the shared boundary once, and preserve annotations attached to the correct original item. Useful discriminating cases are nonmergeable neighbors, one merge, a chain of three, an empty group, and a shared boundary with attached metadata. Check complete output order and membership, not just its length. Repeated application should satisfy the public idempotence policy rather than duplicating the pivot or losing attached data.

<a id="shared-representations"></a>

## Migrate representations through producers and consumers

A representation change affects producers, consumers and public adapters: iterators, generators, parsers, constructors, serializers, persistence, and query projections may all participate. Follow one value through each changed boundary. If a public iterator now yields structured records, a parallel iterator that still yields raw scalars may be intentional compatibility or a missed migration; inspect its contract rather than deleting it automatically.

When a shared processor handles multiple document kinds, distinguish a field absent for one kind from a field renamed for another. Exercise an unchanged kind as well as the new one. A codec round trip alone says nothing about storage, query or public effects; select a pipeline check that reaches the changed consumer. Updating expected tests is justified by an explicit contract change, not merely by a new implementation's output.

For map lookups, derive the key from the map's declared identity: whole composite ID, component ID, or canonical serialized key. TypeScript arrays compare by reference as `Map` keys, so two equal-looking tuple values are not interchangeable identities. Do not always choose the second component: use the repository's existing key function and test two records sharing one component but differing in another.

<a id="callers-and-accounting"></a>

## Propagate changed interfaces and accounting

When parameters, return values or result fields change, search production callers, wrappers, mocks, tests, platform files and generated interfaces. A successful production build does not compile all test callers. Update the producer and each affected consumer; for a new result such as usage, distinguish per-operation data from cumulative totals and propagate it only along the paths that own it. Observe empty/short-circuit, success, retry, cancellation and partial-result paths separately.

Check the exact requested type/function names as well as structural compatibility. Prefer the existing result object and interface unless a new abstraction has a concrete role. Record missing compilation coverage honestly if dependencies or target platforms are unavailable. Formatting and grepping are useful observations, but neither demonstrates signature compatibility.

<a id="fixture-and-check-scope"></a>

## Know what an available check establishes

Resolve fixture paths against the process's real working directory. In ordinary `go test`, package tests run from the package source directory, so a relative fixture path can refer somewhere different from a repository-root script. Inspect the actual file-open operation and available fixtures; do not invent a hidden test's layout.

Existing tests, supplied verifier scripts and explicit requirements are complementary evidence. A hidden grader or private test patch may not be available during solving. Use only exposed checks and state their scope. If requirements and fixtures conflict, isolate the disagreement instead of silently choosing whichever yields green output. A focused test can be sufficient for one boundary while leaving others untested; a full suite may be expensive or blocked. Preserve exit status and the complete output in a file, then inspect excerpts without treating the excerpting command's success as the test verdict.

A self-written fixture should challenge a public contract rather than imitate private implementation choices. Entity matching especially benefits from an independent fixture of the real return shape and a near-match that must not create or return the wrong identity. A formatter, field-existence check or echo of PASS does not exercise that contract.

<a id="source-cleanup-compatibility"></a>

## Avoid mistaking an unobserved use for dead code

A compiler can identify unused imports and local variables; it cannot prove a public API is unused by external consumers. Reflection, plugins, generated bindings, build tags and tests may provide uses that a text search misses. Remove incidental code when its purpose is understood and the task allows it, not as an obligatory cleanup stage. Keep intentional test helpers and exported compatibility surfaces when required. A failed build is not repaired by renaming helpers or suppressing diagnostics; report the actual compiler result.

<a id="linked-record-index"></a>

## Parse linkage fields into typed keys

For linked records with a tag, occurrence number and optional suffix, parse those components separately and index by the contract's pair rather than concatenating whole raw strings. In MARC reciprocal linkage, an alternate field names its source tag and occurrence; a source field can name the alternate tag with the same occurrence. Verify both directions from the actual data standard and retain script/orientation suffixes separately. A regex match starting at zero does not identify the suffix start: use a named capture or the match's end.

A reusable parser can first full-match a documented form such as `(?P<tag>[0-9]{3})-(?P<occ>[0-9]{2})(?:/(?P<suffix>.*))?`, then return a tuple. Whether that form covers all valid inputs is a contract question. Handle repeated links, duplicate occurrences, missing partners and order explicitly. Do not collapse multiple valid alternate fields into a one-value dictionary unless the format guarantees uniqueness. Retain conventional bracketed values when punctuation is part of the data, not a delimiter.

<a id="nullable-migration"></a>

## Carry nullability across the whole representation

When SQL NULL becomes a pointer/nullable wrapper rather than a zero value, trace scans, struct literals, serializers, comparisons, accessors and copy paths. Verify NULL, a present zero and a nonzero value separately. New pointer fields can require updates to callers and tests even if a top-level package still builds. Do not replace every zero with nil: the public contract determines whether they are distinct. Prefer a representation already used by neighboring fields, and validate the emitted public form as well as database scanning.

<a id="encoded-read-write-pair"></a>

## Pair encoded reads with the correct writer mode

Reading an encoded component and passing that text into a decoded-component setter can encode `%` again; `%3A` then becomes `%253A`. Conversely, decoding and rebuilding can change reserved delimiters or unrelated bytes. There is no universal "always use FullyEncoded" rule for Qt `QUrl`: the getter mode, setter parsing mode, component, Qt version and required preservation contract must agree. Follow any explicitly requested getter/representation contract.

Use the installed API's documented mode or a verified whole-URL encoded reconstruction when preserving encoded bytes. Check the resulting URL bytes independently after the setter, not just the intermediate string. Include encoded reserved characters, literal percent, Unicode, query/fragment boundaries, and unchanged neighboring text. A minimal example should establish that a no-op read/write preserves the required representation before adding arithmetic or replacement logic.
