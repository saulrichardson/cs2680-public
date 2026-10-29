# Replay fixtures

`flat`, `flat-allowed`, `failed`, `subagent` and `subagent-forward` are sanitized
test recordings of a small demonstration repository. Event ordering, tool-call
identities, completion state, patch examples and reported counters are retained so
the reducer, delegation graph and replay tests still exercise the same behavior.

Hook output, injected skill instructions, local plugin metadata and model reasoning
have been removed, and the original workstation
path has been replaced with `/tmp/example-project`. These fixtures are incomplete
test inputs, not original development transcripts or evidence of current model
behavior. Anonymous event identifiers are retained to preserve their relationships.

The generators produce `synth-2000.jsonl` and `team-cafe.jsonl` on demand. To run them
from `assignment1/`, use `pnpm fixtures:synth` and `pnpm fixtures:team`.

Personal author details in captured Git output are replaced with example values.
