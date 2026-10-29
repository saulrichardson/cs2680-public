# Contributing and local verification

Each assignment owns its runtime, dependencies, and technical guides. Start with
its README to understand the behavior being changed, then follow the linked design
document into the relevant source. The repository root explains how the projects
relate; it does not define a shared application or build system.

CI checks source and selected execution paths without launching cloud infrastructure.

## Documentation changes

Keep entry pages readable for someone who has not seen the development history.
Explain the purpose and behavior before introducing internal terms. Put commands
next to their prerequisites, state the directory they run from, and link to the
guide that owns the technical detail. Research reports retain their historical
scope; current behavior belongs in the maintained runtime guides.

For prose-only changes, review the diff and run `python3 scripts/check_export.py`
from the repository root. It checks local Markdown targets as well as the curated
file policy. Application test runs are not needed for wording changes.

## Checks available in this checkout

Use Python 3.12 or newer, Node.js 22 or newer, and pnpm 10. From the repository root:

```bash
python3 scripts/check_export.py
cd assignment1
pnpm install --frozen-lockfile
pnpm typecheck
pnpm exec vitest run \
  tests/core/agents.test.ts tests/core/derive.test.ts \
  tests/core/patches.test.ts tests/core/reducer.test.ts \
  tests/core/schemas.test.ts tests/server/replay.test.ts \
  tests/server/router.test.ts tests/ui/rows.test.ts tests/ui/store.test.ts
```

The export checker validates Python syntax, fixture JSON, local Markdown link
targets, the selected file inventory, and common credential patterns. The TypeScript
and replay checks exercise Mission Control's event processing and the server/UI
paths that consume its fixtures. They do not require course credentials or paid
model calls.

The [CI workflow](.github/workflows/validate.yml) runs these checks on pushes and
pull requests. It also installs Assignment 4's pinned runtime dependency and runs
one packaging test to check that the frozen runtime imports successfully. These
checks establish specific source and execution properties; course grading, live
model behavior, browser integration, and cloud deployment need their own environment.

## Work that needs external resources

MADS accepts course task and test datasets through `TASKS_JSON` and `TESTS_JSON`.
Assignment 3's course starter environment and private development helpers are
absent. Assignment 4's [verification guide](assignment4/test/teamharness/README.md)
describes focused checks for runtime changes. Its broader experiment campaigns
are separate operations.

Keep credentials, raw transcripts, and private evidence outside the checkout.
Only the reviewed replay files under `assignment1/fixtures/` may be tracked as
JSONL. Their [notes](assignment1/fixtures/README.md) describe what was sanitized
and which examples are synthetic.

## History checks

Run `python3 scripts/check_export.py --history` to inspect every reachable file
version and commit identity for the same credential and contact-address patterns.
Example addresses are allowed in tests; personal contact details are not.
Private export reports belong outside the checkout.
