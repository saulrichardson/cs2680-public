---
name: verify-contracts-and-tests
description: Check exact caller-visible interfaces and derive expectations independently of a repair. Use for specified signatures, error shapes, return values, compatibility changes, fixture migrations, or local tests that disagree with external results.
---

# Verify the contract the caller actually uses

A repair can implement the intended algorithm and still fail because it exposes the wrong callable, returns the wrong wrapper, or converts a required error into an incidental exception. Use this skill when the task names an interface or describes behavior precisely enough that a consumer can distinguish two plausible implementations. The relevant precision may be semantic, such as which entity receives a result, or literal, such as a required method name and argument order.

## Separate the promised behavior from your design

Read the original requirements and locate their public entry points in the current source. Distinguish a mandated interface from a suggested internal organization. A new helper is a design choice; the function a consumer is instructed to call remains an obligation. Retaining its old signature while placing the new behavior under another name can preserve local callers and still violate the task.

The opposite mistake is treating every old test as a requirement that must remain unchanged. Some tasks intentionally change behavior, rename an interface, or retire an old operation. Determine which consumers retain a promise and which are part of the migration. Preserve exact spellings, defaults, keyword names, error types, and returned shapes when specified. Avoid manufacturing compatibility wrappers merely to make an obsolete test compile if the requested change excludes that compatibility.

A small consumer can expose this boundary cheaply. Compile or call the stated interface using the expected argument positions or keyword names. Assert the type and value the caller receives, including decorators and adapters. If an endpoint promises a JSON string inside a returned wrapper, checking an inner dictionary leaves both serialization and wrapping unresolved.

## Give an expectation an independent reason

An expected result should follow from the public contract, a justified existing behavior, or an independently understood format or protocol. The new implementation's output can help discover what it does; it cannot by itself establish what it should do. This is especially important for generated fixture files, complex matching logic, and stateful event sequences where a large output can look reasonable while changing an important association.

Imagine a parser that adds alternate labels to records. Its revised output includes an additional primary entity as well as the requested label. Regenerating all expected records from that output makes the local suite agree with the parser but does not resolve whether the extra primary entity is permitted. Construct a small record in which primary ownership and an added association differ, derive the expected result from the existing selection policy, and test that distinction before migrating broad fixtures.

Independent derivation does not require a different person or another agent. It requires an argument that does not assume the implementation is correct. An independent investigator can help when available, particularly if given the task and input without the author's explanation. The host determines whether such assistance exists and how workspaces are shared.

## Diagnose a failed check at the right layer

Test discovery, imports, compilation, execution, assertion evaluation, and cleanup can fail separately. Inspect the actual process output and completion state. A command piped into a successful display filter may report zero while the test failed. An empty selection of tests may say nothing about the modified behavior. An early panic can prevent later checks from executing, even when a grader reports many required failures.

When a local test and an external result disagree, compare their source versions, installed dependencies, entry points, inputs, and expectations. Some evaluators restore original tests or replay a patch in a fresh environment. Others evaluate the working tree directly. Use the host's documented facts rather than assuming either behavior. A changed local test is relevant evidence under its own conditions, not a reason to reinterpret an external receipt.

Mocks should preserve the semantics the test relies on. A database fake that treats an OR query as an AND query can blame correct matching logic; a fake accepting positional arguments can conceal a keyword-only interface requirement. Match query structure, ordering, absence behavior, and errors at the existing seam. For details of candidate retrieval versus identity decisions, see [record matching](../parse-and-match-records/SKILL.md).

## Change expectations when the contract warrants it

A legitimate expectation migration explains a changed promise and derives the new value independently. Retain a focused check for the intended new behavior and for the old behavior that remains supported. If the old assertion rejects a newly required result, document the conflict rather than bending the implementation back to the obsolete expectation.

When the assertion is valid, fix the implementation. Weakening a metadata assertion after observing lost metadata, removing an encoding case because the new setter changes it, or accepting a different error because the implementation happens to raise it all remove useful challenges. Before changing either side, identify the precise condition that makes the original expectation too strong or the implementation too weak.

Do not require every existing test to pass merely to satisfy a procedure. Some are outside the requested migration, some cannot run in the current environment, and some may encode an unsupported assumption. State which conclusion the evidence supports and which gap remains. A successful check becomes more useful when its scope is clear.

## Verify after integration, not only during construction

The final candidate may differ from the code a check exercised. A later merge, generated file, wrapper edit, or cleanup can invalidate earlier evidence. Run the consequential checks on the candidate that will actually be returned, using existing receipts when they already identify those same bytes. A hash establishes byte identity; it does not establish that the test was relevant.

Include examples that distinguish a plausible omission: an invalid outer shape as well as malformed JSON, a caller using the required keyword, an absent optional value, a conversion that preserves identity when no change occurs, and a public operation that receives a new absent result. Choose these cases from the actual task rather than multiplying every hypothetical dimension. The result should be a repair whose important claims can be connected to particular observations, without requiring a ceremonial checklist or another reporting stage.
