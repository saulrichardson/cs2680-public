# Inventory sources and active identity

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="platform-inventory"></a>

## Platform inventory and advisory matching

Start with the actual data pipeline: source selection, collection, format recognition, parsing, normalization, filtering, enrichment, matching, and reporting. Record which identity and provenance must survive each boundary.

For multi-source facts, distinguish duplicate source requests, physically identical sources, and duplicate records. Preserve the logical source name for reporting even when canonical paths identify already-read files. Apply source precedence and fallback timing explicitly. A fallback that runs before all primary sources can change which record wins.

For format probing, require enough evidence that the parser accepted the intended format. Silently skipping malformed lines can make a permissive parser falsely win before the correct one is tried. Keep malformed input, unsupported format, empty valid source, and source failure distinct according to the public policy.

For running-kernel filtering, construct identity from distribution family/release, running release, package version/release, architecture, variant, and source/binary grouping as applicable. Installed latest is not running. Broad substring matching can select accessories or a debug variant incorrectly. Retain ordinary non-kernel packages unless the contract changes them.

For advisories, preserve repository and module-stream context into lookup and matching. Keep affected, not-yet-fixed, and publisher resolution state as separate per-package values. A global field overwritten by the last package loses information. Match normalization rules to the ecosystem and return metadata through every caller after changing a matcher contract.

Test source precedence, aliases/symlinks, parser fallthrough, missing helpers, timeout policies, active/inactive variants, unrelated similarly named packages, repository/module mismatch, and two packages with different resolution states. Use a fixed reference date for lifecycle checks. Do not encode current support dates or a universal publisher-state policy from one task.

<a id="platform-inventory-mechanism-details"></a>

<a id="platform-inventory-multi-source-facts-and-package-identity"></a>

### Multi-source facts and package identity

<a id="platform-inventory-details-three-deduplication-scopes"></a>

<a id="platform-inventory-three-deduplication-scopes"></a>

#### Three deduplication scopes

A source request can repeat an alias, two logical paths can resolve to one physical file, and two different sources can report one resource. These are not the same duplicate. Preserve requested order, use canonical identity only for the relevant read-once decision, and retain the original label in provenance. Warnings should be owned by the policy that detects a reportable duplicate, not emitted again at every layer.

For an invented inventory, logical `primary` and `snapshot` sources can refer to the same file, while an external command also reports the same mount/resource. Reading the file once does not imply dropping its first logical label. Record-level first-wins, all-occurrences, or merge behavior must follow the public API. Preserve raw source text if consumers use it for diagnostics.

<a id="platform-inventory-details-format-recognition-before-normalization"></a>

<a id="platform-inventory-format-recognition-before-normalization"></a>

#### Format recognition before normalization

Separate line grammars from file-level recognition. A column parser can incorrectly accept a stanza file if it skips every short line and returns empty success. Reject incompatible structure so the next parser can try, while preserving the contract's malformed-line behavior within an accepted format. A discriminator can apply to the whole file even when not every row has the same distinctive token.

Keep explicit unknown/default values different from absent optional columns. Preserve spacing inside fields when it has meaning. Enrichment should happen for records that survive the required filters; an optional UUID/label helper failing need not turn a valid primary record into a total failure unless required. Memoization of a system-wide lookup must have an appropriate lifecycle/reset seam when tests or callers depend on refresh.

Timeout handling can be error, warning, or omission. Apply it per operation under the contract, and validate invalid timeout configuration at the correct boundary. A helper forwarding an explicit value unchanged and a public entry rejecting that value can both be correct. Test with substituted collectors rather than a live host's incidental state.

<a id="platform-inventory-details-running-versus-installed-kernel"></a>

<a id="platform-inventory-running-versus-installed-kernel"></a>

#### Running versus installed kernel

Model package identity as structured data, not a display name. A modern release string may combine version, package release, architecture, and a variant suffix; another family can use a different scheme. Keep full distribution release text until the family/version parser resolves it. Converting it eagerly to an integer can reject legitimate suffixes.

For binary/source package systems, group by source identity and version when the policy is source-scoped. First determine whether a group contains a running image/header binary; then retain/drop related binaries under the family's rules. Filtering solely by source name can merge inactive versions into the running group. Preserve original identity alongside lookup normalization.

Debian versions have ecosystem-specific epoch/upstream/revision comparison semantics. Source: [Debian version policy](https://www.debian.org/doc/debian-policy/ch-controlfields.html#version). Reuse the project's native comparison implementation; lexical or generic semantic-version comparison is not equivalent.

Test an active image, another installed release, matching companion packages, debug/non-debug or real-time variants, another architecture, accessories with similar names, and an ordinary package. Use the current issue to determine exact family rules. “Any package containing kernel” and “highest version wins” are both inadequate general filters.

<a id="platform-inventory-details-advisory-state-is-per-matched-package"></a>

<a id="platform-inventory-advisory-state-is-per-matched-package"></a>

#### Advisory state is per matched package

Keep `(advisory identity, package identity, repository/module context)` associated with affectedness, fix availability, and resolution. Publisher states such as unsupported, deferred, or under investigation are not universally equivalent to unaffected; map them according to the current policy. An absent resolution entry is a separate case. Do not duplicate a second inconsistent policy in the report aggregator.

For modular packages, a module/stream qualifier can be required for matching; a full build/context label and its compatibility prefix are different identities. A repository may distinguish a base channel from optional streams. Carry the data through request objects into both local-database and remote lookup paths. A correct new field unused by one caller still leaves a false match.

Use two packages attached to one advisory with different states and verify both final entries. Reverse iteration order to catch last-writer metadata reuse. Cover mismatched repository, missing module metadata, fixed and not-yet-fixed cases, unknown publisher state, and ordinary non-kernel behavior. Freeze reference dates in lifecycle tests rather than looking up today's support status during a solve.
