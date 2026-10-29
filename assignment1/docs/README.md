# Mission Control documentation

Start with the [application guide](../README.md) to install Mission Control and try
a conversation. The guides here explain the thinking behind the interface and how
the implementation turns a live agent event stream into a usable application.

| Guide | What you will learn |
| --- | --- |
| [Product intent](product-intent.md) | The problems the interface addresses, the session model, and how a person follows delegated work |
| [Architecture](approach.md) | How CLI events become application state, how the server persists and streams them, and where the code lives |
| [Engineering decisions](records/README.md) | The reasoning behind choices that affect how the application is maintained |
| [Stream JSON reference](reference/claude-stream-json-types.md) | The event shapes used by the parser, reducer, delegation view, and replay fixtures |

For a path through the implementation, read the architecture guide, then follow an
event through `src/core/` into the UI. To work on execution or persistence, continue
into `src/server/`. The application guide's
[code map](../README.md#code-map) links the main entry points.

The [fixture notes](../fixtures/README.md) explain the sanitized and synthetic inputs
used by replay tests. A replay helps exercise event handling; live CLI behavior is
checked separately with a signed-in session.
