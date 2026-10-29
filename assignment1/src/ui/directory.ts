/**
 * The message `projectDir()` in `src/server/trpc/router.ts` throws for `$HOME` and `/`. The dialogs
 * show the same words inline, so refusing a directory reads identically whether the client caught it
 * or the server did.
 */
export const PROJECT_DIR_MESSAGE = 'Choose a project directory, not your home folder or the filesystem root';

/** What the server's `resolve()` does to the two paths we refuse: drop trailing slashes, keep `/`. */
function normalize(path: string): string {
  const stripped = path.trim().replace(/\/+$/, '');
  return stripped === '' ? '/' : stripped;
}

/**
 * Mirrors the server's rule client-side so the Create/Start button can stay disabled instead of
 * round-tripping to an error. An empty value is not a problem, only "not ready yet" — the button is
 * disabled for that separately, without an accusatory message under an untouched field.
 */
export function projectDirProblem(value: string, home?: string): string | null {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const path = normalize(trimmed);
  if (path === '/' || (home !== undefined && path === normalize(home))) return PROJECT_DIR_MESSAGE;
  return null;
}
