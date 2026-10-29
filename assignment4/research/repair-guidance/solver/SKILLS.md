# Find the skill that answers the current uncertainty

The host supplies the reading policy, including any requirement to read relevant skill bodies before implementation. Choose a skill because the uncertainty in front of you matches its subject, then read its body. A catalog entry is navigation rather than the full explanation. Follow technical references selectively when they address that uncertainty; reading every chapter is unnecessary. Portable source paths are relative to the library root supplied by the host; a host may render them as absolute file paths. References remain ordinary files if the host has no special skill mechanism.

## Start from the visible symptom or operation

Use these as navigation questions, then check the actual code. A symptom does not
establish its cause. Change chapters when the evidence changes the question.

| What you can observe | Focused material |
| --- | --- |
| Pasting fills only the first box, focus moves incorrectly, or a render restores old input | [Controlled input, paste, selection and focus](skills/reason-about-async-state/references/event-and-ui-patterns.md#controlled-input-state) |
| An option is accepted but changes nothing in the real operation | [Trace configuration into its consumers](skills/trace-configuration-and-interfaces/SKILL.md) |
| A write succeeds but reading it back changes bytes, type or metadata | [Representation boundaries and round trips](skills/preserve-representations/SKILL.md) |
| An older response replaces newer state, or cancellation and completion disagree | [Asynchronous state and publication](skills/reason-about-async-state/SKILL.md) |
| Local checks pass but a grade still fails, or the tested checkpoint precedes later edits | [Evidence that distinguishes explanations](skills/investigate-with-evidence/SKILL.md) and [exact contract verification](skills/verify-contracts-and-tests/SKILL.md) |

## Investigation and evidence

[Investigate with evidence](skills/investigate-with-evidence/SKILL.md) helps when a theory remains plausible despite failures, repeated checks no longer change the repair, or an aggregate grade is unexplained. It develops competing explanations, probes that preserve the disputed boundary, handling contradictory results, and useful independent assistance. Read it when the difficulty is deciding what observation would actually advance the investigation.

[Verify contracts and tests](skills/verify-contracts-and-tests/SKILL.md) addresses exact names, signatures, return shapes, errors, and the independence of expected results. It explains how a locally successful helper or regenerated fixture can miss the public contract, and how to justify a genuine expectation migration. Read it when implementation choices, old tests, or source checklists are being mistaken for the task's required behavior.

## Values, interfaces, and records

[Preserve representations](skills/preserve-representations/SKILL.md) follows meaning through conversion, wrappers, encoding, serialization, and the final consumer. It develops real getter/setter round-trips, multipart artifacts, metadata on containers and elements, and changes that introduce absence. Read it when an intermediate value looks right but the public result differs in bytes, type, identity, or provenance.

[Trace configuration and interfaces](skills/trace-configuration-and-interfaces/SKILL.md) connects public input to decoding, validation, defaults, construction, and runtime behavior. It covers actual schema representations, dependency and signature changes, alternate callers, and construction effects. Read it when a declared field has no effect or a change works through one path but disappears in another.

[Parse and match records](skills/parse-and-match-records/SKILL.md) explains grammar recognition, selective normalization, typed linkage, multiple readers, candidate retrieval, identity evidence, and ordered grouping. Its technical chapters include structured wire and catalog-style examples without prescribing another project's schema. Read it when strings are recovered but attached to the wrong entity, records are selected incorrectly, or ordering changes semantics.

## Time, state, and ownership

[Reason about asynchronous state](skills/reason-about-async-state/SKILL.md) develops state ownership through event order, UI propagation, timers, streaming counters, stale completions, and deferred work. It explains controlled event sequences and the limits of sleeps, scheduler assumptions, and callback-existence checks. Read it when isolated callbacks look correct but their interactions produce the wrong visible behavior. For **segmented input, independent input/change/paste events, selection, deletion, and focus**, continue directly to [Controlled UI state and user events](skills/reason-about-async-state/references/event-and-ui-patterns.md#controlled-input-state). That section covers real DOM events and controlled prop updates rather than only a final snapshot.

[Repair storage and reconciliation](skills/repair-storage-and-reconciliation/SKILL.md) separates cache policy, identity, pending work, retained outcomes, persistence, migration, indexing, and desired-state effects. It includes request-policy propagation and implicit defaults. Read it when repeated requests, stale data, historical records, or state comparison produce the wrong reads, writes, or commands.

[Inspect platforms and resources](skills/inspect-platforms-and-resources/SKILL.md) follows source collection, active package identity, native protocols, process outcomes, acquisition, ownership transfer, rollback, and cleanup. It explains why installed state, physical resource release, and retained status records are different facts. Read it when platform versions, resource lifetime, or environment assumptions affect the public operation.

[Preserve identity and trust](skills/preserve-identity-and-trust/SKILL.md) keeps caller identity, resource identity, usable credentials, cryptographic purpose, and authorization distinct. It develops key/certificate pairing, entity-specific secret forwarding, provider selection, password verification, and signed identity versus presentation. Read it when correct bytes or a valid signature can still belong to the wrong entity, permission, or operation.

## Following a question across chapters

A repair can cross several subjects. If an optional configuration field is forwarded correctly but its cached result belongs to another identity, follow the state or trust chapter rather than adding more configuration plumbing. If an asynchronous parser publishes a stale result, use the record chapter for its semantics and the event chapter for admission of the completion. Cross-references identify these boundaries; they are not instructions to read the entire library.
