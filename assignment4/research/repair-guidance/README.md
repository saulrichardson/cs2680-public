# Portable repository-repair guidance

This library provides an investigation foundation and nine technical skills for
repository repair. The material explains how to choose informative checks, follow
interfaces and state through a program, preserve representations, and revise a
diagnosis when observations contradict it.

Start with [SYSTEM_PROMPT.md](solver/SYSTEM_PROMPT.md) for the foundation and
[SKILLS.md](solver/SKILLS.md) for the catalog. Each skill links to its supporting
references. Those examples explain general mechanisms; they do not claim that a
particular diagnosis applies to a new repository.

The [integration guide](integration/ADAPTER.md) explains how to combine this material
with a host's tools and lifecycle instructions. The
[evaluation guide](integration/EVALUATION.md) explains how to assess delivery and
behavior. The library's presence in a package is not evidence of a performance gain.

The package builder reads `manifest.json` and verifies the hashes of the complete
`solver/` tree before copying it. That tree contains the runtime guidance; the
integration documents are for maintainers. Private source inventories, benchmark
coverage records, and campaign traces are not included.
