---
name: parse-and-match-records
description: Preserve grammar, linkage, identity, and order while parsing or matching records. Use for alternate representations, multilingual data, reader parity, entity resolution, grouped results, or ambiguous normalization and selection rules.
---

# Preserve meaning before choosing a convenient representation

A parser can recover every interesting string and still produce the wrong public record because it loses linkage, merges identities, changes order, or applies normalization at the wrong stage. Use this skill when the input has a grammar, repeated or linked fields, alternate scripts, or a distinction between finding a candidate and deciding it is the same entity.

## Separate recognition from interpretation

Establish the accepted input language and the required output or error. A regex finding a valid substring inside invalid input is not equivalent to validating the complete value. A broad digit or whitespace class can accept characters excluded by a narrower ASCII contract. Recognizing a unit prefix while ignoring trailing text can admit an invalid measurement. Conversely, tightening a pattern can reject a supported legacy form.

Locate the public importer, its decoder, tokenizer, parser, semantic conversion, and output construction. Decode at the boundary the format defines; decoding an escaped delimiter before splitting can change structure. Preserve original payload when normalization is only for comparison. Syntax keywords can be case-insensitive while names, paths, unit symbols, or opaque identifiers remain case-sensitive.

Use pairs that differ in one relevant property: accepted versus trailing content, literal versus escaped delimiter, ASCII versus another character family, missing versus empty, or a supported alternative reader. Inspect full outputs and required errors. The [grammar reference](references/grammar-and-normalization.md) develops these cases and explains why neither universal normalization nor universal preservation is sufficient.

## Build associations from the format's identity

Repeated fields are not necessarily positional pairs. A primary field can link to one or more alternate representations using a typed key, occurrence number, or reverse reference. Collect the relationship before formatting the names or descriptions. Check the whole linkage syntax rather than accepting a matching prefix that belongs to another occurrence.

Alternate text should remain attached to the entity it describes. Consider two primary records with different alternates, an unmatched alternate, a malformed link, and duplicate values. A test that merely finds the alternate string somewhere in the final output can pass while the association is wrong. Preserve the distinction between no valid alternate and a valid alternate whose normalized text duplicates the primary value when the public policy requires it.

Binary and text readers can expose similar objects with different iterator or subfield behavior. Define the common semantic operation and verify it through each real reader. An adapter returning the first matching value can lose repeated fields even when its method name looks compatible. Read [linked records and reader parity](references/linked-records.md) for structural linkage, alternate scripts, punctuation, dates, and import fields.

## Keep local normalization within its intended scope

A separator at the edge of a field and punctuation inside a person's name can have different meanings. A rule removing a trailing period may preserve a single-character initial while removing a longer token's terminator. Do not infer abbreviation behavior from an English description when an existing helper or actual input can settle it.

Shared normalization is useful when several readers promise the same semantics, but centralizing it can accidentally change organizations, events, or unrelated record categories. Determine the inputs and entity types that the task actually changes. Retain existing selection and contributor policies unless the public change includes them. If wording is ambiguous, construct a small record that distinguishes the interpretations and compare it with the established consumer behavior.

## Distinguish candidate retrieval from identity evidence

A query can retrieve plausible candidates without proving that any one is the correct entity. A broad OR query followed by prioritized matching differs from an AND query that filters out useful alternatives before the matcher sees them. Mocks need to preserve the query semantics and ordering relied on by the real implementation.

Define which evidence is strong, missing, or conflicting. A name variant, an alternate script, a surname, and birth/death dates can contribute different kinds of evidence. Missing dates should not automatically be treated as contradictory dates, and a conflicting known date should not disappear behind a convenient name match. Evaluate the supported evidence for each candidate and then apply the documented priority and tie policy.

Use several candidates so the decision rule is exercised, including one with a stronger name match but conflicting metadata and one with incomplete but non-conflicting evidence. Check deterministic choice or deliberate ambiguity as the contract requires. The [matching and ordered output reference](references/matching-and-order.md) develops candidate/query fidelity, missing versus conflicting evidence, and ordered projections.

## Preserve adjacency, ownership, and annotations while grouping

Grouping adjacent results requires the order actually traversed. A search can collect results newest-first and later display them oldest-first; merging against the wrong neighboring item can join non-adjacent events. Each group's annotations, identifiers, highlighting, and context belong to particular source items even when their presentation is combined.

Choose a sequence with overlapping context windows, adjacent matches, and one separating non-match. Verify both the grouping and the metadata associated with each item. For aligned catalogs, preserve the association between keys, labels, values, and persisted identities while sorting or filtering. A single sorted list can be correct while its companion arrays become misaligned.

## Treat fixture migrations as a separate decision

A new parser output may require new expectations when the task changes semantics. Derive the expected record independently before regenerating a large fixture set. Otherwise, an added primary entity, dropped contributor, or incorrectly associated alternate can become the generator's new truth and turn a local suite green without resolving the contract.

Preserve representative input records and the reasoning for the expected association or selection. Verify the same cases through the final importer and public model, not just a new helper. This lets a legitimate migration remain well supported while retaining counterexamples that can reject an appealing but incorrect interpretation.
