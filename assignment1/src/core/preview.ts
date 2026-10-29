/**
 * How the console may show one workspace file, and — for the ones it renders as documents — the
 * exact sandbox that makes doing so safe.
 *
 * In `core` because both halves of the decision have to agree and they live on opposite sides of
 * the wire: the server sets the `Content-Security-Policy` on the response, the browser sets the
 * `sandbox` attribute on the frame. Two independently maintained copies of "which kinds may run
 * scripts" is exactly the drift that turns a defence into a comment.
 */
export type PreviewKind = 'markdown' | 'text' | 'image' | 'document' | 'none';

/**
 * The `sandbox` attribute for an `<iframe>` showing this kind.
 *
 * `allow-scripts` **without** `allow-same-origin` is the whole of it. The document then has a unique
 * opaque origin: its own scripts run — which a generated plan with layer toggles needs, and a
 * preview whose controls are dead is a misleading preview — while it cannot read Mission Control's
 * pages, cookies or storage, or call its endpoints. Everything else stays ungranted, so there are
 * no forms, no popups, no pointer lock and no top-level navigation.
 *
 * Adding `allow-same-origin` here would silently undo all of it. It must never be added.
 */
export function previewSandboxFor(kind: PreviewKind): string {
  return kind === 'document' ? 'allow-scripts' : '';
}
