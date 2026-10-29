# Linkage, multiple readers, and imported records

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

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

Read MARC and import records for ordered subfields, linkage grammar, reader parity, names, and downstream import integration. Use [entity resolution](matching-and-order.md#entity-resolution) for candidate selection; a correctly linked alternate field does not itself identify an existing database entity.

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

<a id="linked-record-index"></a>

## Parse linkage fields into typed keys

For linked records with a tag, occurrence number and optional suffix, parse those components separately and index by the contract's pair rather than concatenating whole raw strings. In MARC reciprocal linkage, an alternate field names its source tag and occurrence; a source field can name the alternate tag with the same occurrence. Verify both directions from the actual data standard and retain script/orientation suffixes separately. A regex match starting at zero does not identify the suffix start: use a named capture or the match's end.

A reusable parser can first full-match a documented form such as `(?P<tag>[0-9]{3})-(?P<occ>[0-9]{2})(?:/(?P<suffix>.*))?`, then return a tuple. Whether that form covers all valid inputs is a contract question. Handle repeated links, duplicate occurrences, missing partners and order explicitly. Do not collapse multiple valid alternate fields into a one-value dictionary unless the format guarantees uniqueness. Retain conventional bracketed values when punctuation is part of the data, not a delimiter.
