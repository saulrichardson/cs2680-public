/**
 * A project's folder in a URL.
 *
 * A folder path is full of slashes, so it cannot be a path segment as it stands. Percent-encoding
 * the whole thing leaves `%2F` for the router to decode differently from the browser, so the path
 * is encoded to base64url instead: one opaque segment, decoded in exactly one place.
 *
 * Written with `TextEncoder`/`btoa` rather than `Buffer`, because this runs in the browser as well
 * as on the server — a client component reaching for a Node global works under SSR and then throws
 * the moment a link is clicked. Both halves go through UTF-8 explicitly, so a folder called
 * `Café 100%` survives the round trip.
 */
export function projectHref(root: string): string {
  return `/projects/${encodeProjectRoot(root)}`;
}

export function encodeProjectRoot(root: string): string {
  const bytes = new TextEncoder().encode(root);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * The folder a project segment names, or `null` when the segment is not one of ours.
 *
 * A stale bookmark or a hand-built link reaches the route as something `atob` refuses, and an
 * exception there is a server error page. `null` lets the page say the project is not here.
 */
export function decodeProjectRoot(segment: string): string | null {
  try {
    const binary = atob(segment.replace(/-/g, '+').replace(/_/g, '/'));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    const root = new TextDecoder().decode(bytes);
    // Every project root is an absolute path; anything else is a segment we did not write.
    return root.startsWith('/') ? root : null;
  } catch {
    return null;
  }
}

/** The folder's own name, which is what an operator calls the project. */
export function projectLabel(root: string): string {
  return root.split('/').filter(Boolean).pop() ?? root;
}
