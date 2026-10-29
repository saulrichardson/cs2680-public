# Implementation scope

This checkout contains the maintained Python repair harness. Its entry point is
[the assignment overview](../README.md); the
[runtime design](../src/teamharness/DESIGN.md) explains its lifecycle.

The runtime lives in `src/teamharness/`, with shared HTTP, storage, transport, and
process/file modules directly under `src/`. The course adapter in
`lab/adapters/teamharness/` packages those components and connects them to external
task and grading infrastructure. It does not supply the solver with a second
investigation workflow.

The retained lab utilities support packaging, environment preparation, observation,
and evidence storage. They require explicit external configuration. This repository
contains no live resource inventory, private campaign ledger, or alternate project
checkout. Start with the adapter guide before using infrastructure utilities.
