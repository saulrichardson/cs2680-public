# Exact obligations and useful checks

Use this when a task changes an interface, rendered output, input validation, or
behavior that can look correct while missing a literal requirement.

Begin with the original task. Separate required behavior from a plan's design
choices. Locate the real public entry point and follow it to the observable
result. Preserve exact names, values, types, return shapes, and error behavior
where the task specifies them. Do not assume all wording is incidental.

For every important obligation, choose an observation that would distinguish a
plausible wrong implementation. A source pattern can locate code; it rarely
establishes the complete caller-visible contract. In a UI, check the actual
rendered element and interactions. In a service, exercise the caller-visible
request and result. In a parser, assert the returned structure and errors.

An invented example: a task requires `data-testid="option-choice-17"`. The
component already renders `id="option-choice-17"`. Searching for
`option-choice-` succeeds but does not establish that `data-testid` exists.
Query or inspect the attribute by its exact name and value. Also check whether a
wrapper duplicates the child's supposedly unique test identifier. A query that
finds two matches exposes a different defect from a query that finds none.

Structured input has several boundaries: decoding, outer shape, field presence,
field type, and semantics. `json.loads("[]")` succeeds but returns a list. Calling
`.get` on it is not valid object handling. Depending on the task, distinguish a
malformed document, a valid document of the wrong shape, a service-error object,
a missing field, an empty value, and a valid empty result. Do not flatten these
into one case without a contract reason. Exercise the application's actual
entry point, not only a helper disconnected from that path.

The test author should independently derive expectations from the task before
seeing the implementation's argument for correctness. Prefer a small assertion
that rejects a concrete omission over a large suite that mirrors the candidate.
Where meaningful, demonstrate the missing behavior on the unchanged baseline.
Failure to collect or compile a test is not that demonstration.

When a test conflicts with the task, investigate the conflict. Tests can be
incorrect, stale, or overly prescriptive. Keep the original output and explain
which requirement supports a correction. Do not alter expectations merely to
turn red into green. A passing existing suite also does not prove that a new
obligation has been tested.
