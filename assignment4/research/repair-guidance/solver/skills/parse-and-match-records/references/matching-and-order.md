# Candidate evidence and ordered output

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="entity-resolution"></a>

## Entity matching and creation

Build a decision table from the public matching policy: available input evidence, candidate query, acceptance rule, priority, tie-breaker, and no-match behavior. Do not invent a confidence threshold or assume every normalization is allowed. For import-specific rules, see [record import semantics](linked-records.md#domain-marc-and-imports).

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

<a id="ordered-merge-boundaries"></a>

## Trace adjacency in the chosen traversal direction

For an ordered merge, write down which end of each item meets its neighbor in the chosen traversal. Forward iteration naturally compares the previous result with the next input; reverse traversal needs an explicit reversal of that relation. Retain the shared boundary once, and preserve annotations attached to the correct original item. Useful discriminating cases are nonmergeable neighbors, one merge, a chain of three, an empty group, and a shared boundary with attached metadata. Check complete output order and membership, not just its length. Repeated application should satisfy the public idempotence policy rather than duplicating the pivot or losing attached data.
