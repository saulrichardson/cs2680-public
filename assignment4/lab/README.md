# Packaging and infrastructure utilities

The [team harness adapter](adapters/teamharness/README.md) is the entry point for
package construction and external course execution. The local solver runtime does
not import the lab; the builder copies selected course-facing modules into its
output package.

`adapters/teamharness/` contains the course wrapper, environment preparation,
launch, observation, and collection tools. `replica/` provides the supporting
inference, image, budget, and process utilities used by those tools.
`storage/evidence_archive.py` implements archive upload and selective restore.

These are reusable source utilities. Their credentials, resource identifiers,
machine bindings, ledgers, and raw evidence are external configuration and are not
included in the checkout. Building a package is separate from launching resources.
The [runtime design](../src/teamharness/DESIGN.md) describes the behavior that a
package implements; infrastructure scripts do not change that contract.
