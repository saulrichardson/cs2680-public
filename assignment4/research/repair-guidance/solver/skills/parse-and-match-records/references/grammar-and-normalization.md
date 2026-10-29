# Grammar, acceptance, and normalization

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

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

Read [wire formats](../../preserve-representations/references/wire-and-serialization.md#domain-wire-formats) for multipart bytes and encoded-URL scanning; schema and types for coercion and provenance; or [platform inventory](../../inspect-platforms-and-resources/references/inventory-and-advisories.md#platform-inventory) for source recognition and kernel/package semantics. Use the relevant grammar instead of enlarging one generic regex.

<a id="ascii-grammar-boundaries"></a>

## Choose ASCII or Unicode deliberately

In Python string regexes, `\d` includes Unicode decimal digits and `\s` includes Unicode whitespace; neither means "all possible digit-like/space-like characters." In particular, do not assume every zero-width character matches `\s`. If the grammar requires ASCII digits and literal spaces, use `[0-9]` and `[ ]` (or a deliberate ASCII mode), and consume the whole input with `fullmatch`. Decide independently whether leading/trailing space is allowed. Check ASCII digits, an Arabic-Indic decimal digit, non-breaking space, tab, newline, and a valid prefix followed by junk. Broad `.strip()` normalization can erase exactly the evidence a strict grammar must reject.
