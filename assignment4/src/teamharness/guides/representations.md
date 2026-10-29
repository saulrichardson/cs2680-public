# Representation boundaries and round trips

Use this when changing encoded URLs, byte formats, parsers, serializers, schemas,
or conversions where two representations can look similar but mean different things.

Name the representation at each boundary: input bytes, decoded values, intermediate
structure, and emitted bytes or public result. A correct reader does not imply a
compatible writer. Probe the installed library rather than reasoning only from
method names or remembered defaults. Follow explicit representation requirements
in the task; do not replace them with a universal rule.

For URLs, a percent escape is `%` followed by two hexadecimal digits. Digits inside
an escape can be data encoding rather than the number the caller wants to edit.
Choose the component and eligible span before modifying it. Preserve unrelated
components, delimiters, and encoded spelling where required.

Read and write modes must agree. Passing encoded text into a setter that expects
decoded text can encode `%` again. In an invented path, `%2F` can become `%252F`.
Conversely, decoding a reserved delimiter can change the URL's structure. In Qt
QUrl, component formatting flags and setter parsing modes are different kinds of
options. There is no universal "use FullyEncoded everywhere" rule. Examine the
installed getter/setter pair and the task's required representation.

Construct a minimal round-trip probe with the actual API: read a component, edit
one intended value, write it back, and inspect the final serialized URL. Include
an escape next to ordinary digits, unrelated query/fragment content, and the
specific boundary requested by the task. Compare the final output, not merely
the intermediate string. A manual reconstruction may avoid one setter problem
while dropping another component; check that choice against the complete contract.

The same principle applies to serialized schemas and generated source. Escaping
that makes a Python or JSON string valid does not establish that the generated
CUE, TypeScript, or SQL is valid. Parse or compile the emitted artifact with its
real consumer. Grep can confirm that text was written but cannot validate grammar.

Keep representation tests alongside the procedure they justify. Do not normalize
away whitespace, ordering, escaping, or delimiters when the task requires exact
bytes. If a library canonicalizes text, determine whether that is allowed before
declaring preservation. Prefer one working counterexample to repeated speculation.
