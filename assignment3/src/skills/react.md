# Working with React components

A component's behavior includes what it renders, what users can operate, what it
sends to collaborators and how those things change over time. A helper producing
the right value is only one part of that behavior. This guide develops those
connections; the JavaScript/TypeScript guide covers package execution, data and
async testing, and the shared guidance owns the general repair workflow.

## Start with the rendered caller and its environment

Find the component or hook's actual callers and the repository's existing render
helpers. Props, context providers, store initialization, localization and routing
can all affect a test before it reaches the changed behavior. Reuse relevant setup
without inventing extra props or state that the original caller never supplies.
When the task intentionally migrates a prop or interface, update affected callers
coherently. Otherwise keep their supported behavior working. An optional callback
may need an optional call; a required collaborator may instead need valid setup.
Do not add a default merely to hide a broken connection.

Read the installed React and test-library versions and the configured environment.
Some repositories use jsdom, others a browser or different rendering tools. jsdom
does not provide browser layout, and individual browser APIs may be absent or
stubbed. Inspect setup before diagnosing an observer, size or focus result.
Stylesheet handling also depends on the runner; a mocked stylesheet cannot prove
that a class visually hides an element in a browser.

Choose an observation that matches the requirement. Presence in the DOM,
visibility, accessibility-tree presence and enabled interaction are different
properties. Hiding an element may preserve state and be intended; unmounting may
be required to remove it or dispose of its effects. A `queryBy*` returning null
checks absence under that query, while a visibility assertion checks another
condition. Do not turn a particular test convention into a universal rule that
anything described as closed must be unmounted.

Queries can help reveal what a user can operate. A label, accessible name, role,
text and test ID are different selectors; use the one the public contract or
existing caller establishes. If the task names an exact attribute or string,
preserve it. Otherwise avoid adding identifiers simply to match an imagined test.
A newly introduced wrapper may affect layout, semantics or snapshots; inspect why
those differences are needed rather than automatically accepting or rejecting
all markup changes.

## Derive state from the event that changes it

Trace where each value comes from and who can update it. A value derived entirely
from props may be computed during render. A value maintained by a store or event
source needs a subscription or another mechanism that makes updates visible to
React. Local interaction state may need to survive several events. Choose the
ownership that matches the required lifetime instead of copying every value into
state or every side effect into an effect.

For an event-backed hook, determine whether the event carries the new value or
only announces that a getter changed. Those are different contracts. If the event
is documented to carry the new status, this pattern illustrates the relationship:

```js
const [status, setStatus] = useState(() => client.status);
useEffect(() => {
  const onStatus = (nextStatus) => setStatus(nextStatus);
  client.on('statusChanged', onStatus);
  return () => client.off('statusChanged', onStatus);
}, [client]);
```

Use the actual event and repository subscription API. If the event instead
signals invalidation, the handler may need to read the getter. Check changing the
source after mount, switching to another source instance and unmounting. A test
that merely renders the initial getter value cannot establish subscription or
cleanup. The example does not settle a race between an initial read and attaching
a listener; inspect whether the project's subscription primitive handles that
when it matters.

An effect's dependency array describes when React should rerun that effect after
render. React compares individual dependencies; a local state update that leaves
them unchanged does not by itself require that effect to run again. Remounting,
changed object identities and development checks are separate causes to inspect.
Before adding a ref guard to suppress an alleged loop, observe the effect calls
and dependency values. A guard can accidentally suppress a later legitimate
update.

Closures capture values from the render that created them. Missing a dependency
can leave a handler or effect using old data; adding an object recreated on each
render can cause extra work. Follow the actual identity and lifecycle before
choosing memoization, a functional state update, a ref or a different owner.
For `setItems(previous => ...)`, preserve immutability when the store or React
uses identity to detect changes. A ref can retain mutable state without causing
a render; that is useful only when visibility does not depend on the mutation.

A later writer can undo a correct local calculation. Follow loading, local edits,
effects and saving to see which writer establishes the final observable value.
For example, an empty list can mean not loaded, loaded with no entries, or an
explicit clear. Read the state model before guarding all work with `list.length`.
A cleanup helper for stale entries may need to run on a loaded empty list even
though it should not run during loading. Test the states the contract distinguishes.

## Exercise interactions as sequences

An interaction simulator and dispatching a single event can exercise different
paths. `fireEvent` sends the event you request; a user-event library models a
larger interaction sequence according to its installed version. A paste, keyboard
input, deletion and programmatic value change can produce different events and
payloads. Read how this component receives them and what behavior the task
promises. Neither testing style reveals which hidden events will be used.

For an input, observe the value, callback payload and focus after the relevant
sequence. Typing several characters at once can differ from typing one at a time;
removing the final character can differ from changing one nonempty value to
another. Choose such examples when they expose the specific required behavior,
not as a mandatory catalog for every input. Browser attributes such as `maxLength`
can constrain user interactions without constraining all synthetic assignments.
Determine where normalization belongs so supported callers see the promised result.

Controlled and uncontrolled inputs have different ownership. A controlled `value`
comes from React; changing the DOM value without updating its owner can be undone
on the next render. A parent may reject or transform a proposed value. Exercise
the component with that parent contract or a faithful wrapper, and inspect both
what was requested and what was rendered. A test that manually forces the desired
prop immediately after an event may bypass a missing callback.

Focus and refs have their own timing. Inspect the project's ref-forwarding pattern
and installed version. An imperative focus operation requires the target node to
exist. Replacing an element or changing its key can lose focus and local state.
Stable keys matter when identity should survive reordering; an index can associate
state with the wrong item in an editable list. Choose keys from the identity the
application actually tracks.

## Observe async completion and disposal

When work waits for a condition, find the code that observes that condition and
restarts or releases the work. It may be an effect, subscription, action or other
existing scheduler. Adding a guard without arranging a later trigger can prevent
work permanently. Adding a new trigger alongside an existing one can duplicate it.
A controllable dependency lets a test hold the operation pending, change the
condition and then observe the resulting call or rendered state.

Await the actual visible consequence using the repository's testing conventions.
`findBy*`, `waitFor` and `act` serve different purposes; follow the installed APIs
and do not treat a fixed sleep as proof that every update completed. A test runner
can finish while an unawaited promise or assertion remains outstanding. Fake timers
and promises may need separate advancement. Inspect what is still pending when an
expected update never arrives.

Cleanup is behavior too. A subscription needs a matching unsubscribe from the
same source and handler. A pending request may need cancellation or protection
against a late update. Observe switching inputs or unmounting while work is
pending when the change affects those cases. A cleanup function's presence does
not establish that it releases the correct resource, and a warning's absence does
not establish that no work remains.

## Check the whole required consequence

Use the real rendering and state path for the disputed behavior, with unrelated
external services replaced as necessary. A test can check a checkbox while
missing that saving serializes the previous value. When persistence is part of the
task, follow load, interaction, save and the next read. Keep distinct states such
as absent preference and explicitly disabled preference through that journey.

Snapshots record a particular rendered result. Inspect their diff for intended
changes, accidental wrappers, lost attributes and compatibility effects. Updating
the snapshot proves only agreement with the new expectation. A semantic check of
the required interaction or output supplies the missing explanation. Conversely,
a changed snapshot can be expected under a deliberate UI change; do not preserve
an obsolete structure merely because the old snapshot contains it.

When an expert proposes a React-specific explanation, check the actual lifecycle
and source premise. A tiny instrumented render can distinguish a stale closure,
a remount and an effect rerun. If the premise is supported, make the correction
and retain that check. If it is not, preserve the useful parts of the review while
returning to the real unresolved behavior. A passing grade for several edits does
not turn every incidental guard or explanation into a reusable React rule.
