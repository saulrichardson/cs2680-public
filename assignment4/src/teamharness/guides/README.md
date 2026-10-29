# Investigation reference library

These are optional, authored techniques. The original public task and recorded
execution determine what is required and what happened. Examples illustrate
mechanisms; they are not answers for the repository in front of you. Read the
bodies that help answer a consequential question, challenge their assumptions,
and use the installed toolchain to check technical details. No reading quota or
mandatory repair sequence applies.

The library contains investigation methods, language references, and executable
examples. It is frozen with each team so continuation and children see the same
content. [The bundle inventory](provenance.json) records hashes of the installed
guidance files. Availability of guidance does not establish that it improves a
model's results.

| Question in the current investigation | Read |
|---|---|
| What would change my explanation? Familiar checks pass but the task remains unresolved | [Investigation method](investigation.md) |
| Exact names, signatures, errors, task intent, independent expectations | [Public contracts](contracts.md) |
| Descriptor versus executor; async order, effects, real process completion | [Effects and time](effects-and-time.md) |
| Missing/null/zero, schemas, serialization and real producer/consumer values | [Representations](representations.md) |
| Real callers, independent tests, patch integration and what a check establishes | [Integration](integration.md) |
| Parsing, escaping, Unicode, linked records, identity, matching and ordered output | [Data and parsing](mechanisms/.agents/skills/data-and-parsing/SKILL.md) |
| Overlap, stale responses, cancellation, UI events, timers, media and streams | [Async, UI and streams](mechanisms/.agents/skills/async-ui-and-streams/SKILL.md) |
| Cache policy, persistence, migrations, read-back and reconciliation effects | [State, storage and reconciliation](mechanisms/.agents/skills/state-storage-and-reconciliation/SKILL.md) |
| Configuration reaches a consumer; defaults, interfaces, trust and routing | [Configuration, interfaces and identity](mechanisms/.agents/skills/configuration-interfaces-and-identity/SKILL.md) |
| Package inventory, processes, native/Qt behavior, ownership and cleanup | [Platforms, processes and resources](mechanisms/.agents/skills/platforms-processes-and-resources/SKILL.md) |
| Language/runtime examples through actual project entry points | [Go](languages/go.md), [Python](languages/python.md), [TypeScript/JavaScript](languages/typescript-javascript.md), [React](languages/react.md) |
| Specific language traps and controlled probes | [Go cookbook](mechanisms/.repair-memory/cookbooks/go.md), [Python cookbook](mechanisms/.repair-memory/cookbooks/python.md), [TypeScript cookbook](mechanisms/.repair-memory/cookbooks/typescript.md), [Native cookbook](mechanisms/.repair-memory/cookbooks/native.md) |
| Invented executable models with explicitly limited contracts | [Reference models](mechanisms/.repair-memory/reference_models.py) |

Useful recurring lessons are to check a literal contract through its real caller,
follow a value across its complete representation path, derive expectations
independently of the repair, and confirm that a check actually reached its
assertions. A cache-only workaround, a setup failure, a source string match or a
reviewer's confident explanation each establishes much less than a working repair.
Use these distinctions to choose better evidence, not to fill out another report.
