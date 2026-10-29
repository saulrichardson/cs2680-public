# Supporting infrastructure utilities

These modules support the [team harness adapter](../adapters/teamharness/README.md).
They provide external process execution, inference serving, image handling, budget
tracking, collection, and observation. They are infrastructure utilities rather
than a second solver implementation.

The caller supplies resource configuration, credentials, paths, and spending limits.
No running-resource inventory or active ledger is included. Use each module's CLI
help to inspect its actual inputs, and preserve the identity of the package and
source under test when combining utilities.

The [runtime design](../../src/teamharness/DESIGN.md) defines investigation and
completion behavior. The adapter guide explains which components enter a frozen
course package and which operations require external infrastructure.
