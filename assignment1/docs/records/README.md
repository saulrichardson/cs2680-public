# Engineering decisions

These rules describe the current implementation. Keep new records brief and limited to decisions that help maintain it.

- **Preserve raw events.** Persist CLI output before deriving presentation. Live execution, replay, and imports share one reducer.
- **Record provenance explicitly.** Execution, replay, and import are distinct run origins. Recorded metrics do not imply new work or usage.
- **Separate outcome from process ownership.** Recording a terminal status does not prove a child exited. Cancellation and shutdown retain ownership until termination is confirmed.
- **Preserve project identity.** Group sessions by their recorded project, including sessions running in isolated checkouts.
- **Keep file and conversation lifecycles separate.** Archive hides a session; replay shows recorded events. Neither operation deletes or restores workspace files.
- **Capture before synthesis.** Wrap-ups receive recorded reports and selected file copies, not live references to changing sources. Keep original sessions and output folders separate; record partial inputs explicitly.
- **Keep agents and tool calls visible together.** Agent cards navigate one level of delegation at a time; children stay with their parent. The tool-call outline remains beside the dialogue on wide screens, including nested calls and jump-to-call navigation. Agent selection must not replace that outline. Narrow columns use a dismissible tool-call drawer.
- **Use stable client snapshots.** Zustand selectors must not allocate a new collection on every snapshot read; derive collections from stable selected state.
- **Keep Git inspection independent.** Comparison uses a temporary index rather than staging into the operator's index. Workspace creation cleans up only resources it owns.

See the [architecture](../approach.md) for implementation boundaries. Exploratory plans, demo production materials, detailed audits, and machine-specific inventories belong outside the repository. Required course session archives remain tracked.
