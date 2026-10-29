import type { Hunk, Patch } from './types';

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/** What a backslash escape in a git-quoted path stands for. */
const ESCAPES: Record<string, number> = {
  a: 0x07,
  b: 0x08,
  t: 0x09,
  n: 0x0a,
  v: 0x0b,
  f: 0x0c,
  r: 0x0d,
  '"': 0x22,
  '\\': 0x5c,
};

/**
 * A path as git wrote it in a diff header, back to the real name.
 *
 * Git surrounds a path in double quotes and C-escapes it whenever the name contains a character it
 * cannot write plainly — a tab, a newline, a quote, or (unless `core.quotePath` is off) any byte
 * above ASCII, as `\NNN` octal. `--numstat -z` reports the same files with their real names, so
 * without undoing this a patch and its FileStat disagree about which file they describe and the
 * diff view cannot correlate them. A path that is not quoted is returned unchanged.
 */
export function unquoteGitPath(raw: string): string {
  if (!raw.startsWith('"') || !raw.endsWith('"') || raw.length < 2) return raw;
  const body = raw.slice(1, -1);
  // Octal escapes are *bytes* of the name's UTF-8, so the result is assembled as bytes: git writes
  // "na\303\257ve" for a name JavaScript holds as five characters.
  const bytes: number[] = [];
  const encoder = new TextEncoder();
  let i = 0;
  while (i < body.length) {
    if (body[i] !== '\\') {
      // Take the whole run up to the next escape and encode it in one go. Encoding character by
      // character would hand `TextEncoder` the halves of a surrogate pair separately — a real case
      // whenever `core.quotePath` is off and a tab or a quote still forces the name to be quoted,
      // which turned "😀\tname.txt" into two replacement characters.
      const next = body.indexOf('\\', i);
      const end = next === -1 ? body.length : next;
      for (const byte of encoder.encode(body.slice(i, end))) bytes.push(byte);
      i = end;
      continue;
    }
    const escaped = body[i + 1];
    if (escaped === undefined) break;
    const simple = ESCAPES[escaped];
    if (simple !== undefined) {
      bytes.push(simple);
      i += 2;
      continue;
    }
    const octal = /^[0-7]{1,3}/.exec(body.slice(i + 1, i + 4))?.[0];
    if (octal) {
      bytes.push(Number.parseInt(octal, 8));
      i += 1 + octal.length;
      continue;
    }
    // an escape git does not produce: keep the backslash as written rather than dropping it
    bytes.push(0x5c);
    i += 1;
  }
  return new TextDecoder().decode(new Uint8Array(bytes));
}

/**
 * The path from a `---`/`+++` header, unquoted.
 *
 * Git appends a tab after an unquoted name that contains a space, so that the header stays
 * parseable; a name that really ended in a tab would have been quoted instead, so stripping one
 * trailing tab can only ever remove that separator. Nothing else is trimmed: trimming would
 * silently rename a file whose name ends in a space.
 */
function headerPath(line: string): string {
  return unquoteGitPath(line.slice(4).replace(/\r$/, '').replace(/\t$/, ''));
}

/**
 * Drop the `a/`/`b/` the diff header puts in front of a path.
 *
 * Only that exact prefix, and only once. Compare asks git for these prefixes explicitly
 * (`--src-prefix`/`--dst-prefix` in `diffAgainstBase`), so a header that does not carry one is
 * describing a file whose own name begins that way — a real `b/` directory — and stripping it
 * would name the wrong file.
 */
function stripPrefix(path: string, prefix: 'a/' | 'b/'): string {
  return path.startsWith(prefix) ? path.slice(prefix.length) : path;
}

export function parseUnifiedDiff(text: string): Patch[] {
  const patches: Patch[] = [];
  let current: Patch | null = null;
  let hunk: Hunk | null = null;
  const lines = text.split('\n');
  // A trailing '\n' in the input produces one bogus '' element from split; drop it
  // so it isn't misread as a blank context line appended to the last hunk.
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
  for (const line of lines) {
    if (line.startsWith('diff --git ')) {
      current = { filePath: '', hunks: [] };
      patches.push(current);
      hunk = null;
      continue;
    }
    if (!current) continue;
    // Inside a hunk, content comes first.
    //
    // A file's `---`/`+++` headers only ever appear *before* its first hunk, but an added line may
    // legitimately begin with `+++ ` — the line `++ b/x` added to a file reads as `+++ b/x` in the
    // diff. Checking for headers first made such a line disappear from the hunk and silently
    // rename the patch after it. `@@` and `diff --git ` are the only things that end a hunk body,
    // and both are checked below (and above).
    if (hunk !== null && (line.startsWith(' ') || line.startsWith('+') || line.startsWith('-'))) {
      if (!HUNK.test(line)) {
        hunk.lines.push(line);
        continue;
      }
    }
    if (line.startsWith('+++ ')) {
      const p = headerPath(line);
      current.filePath = p === '/dev/null' ? current.filePath : stripPrefix(p, 'b/');
      continue;
    }
    if (line.startsWith('--- ')) {
      const p = headerPath(line);
      if (p !== '/dev/null' && !current.filePath) current.filePath = stripPrefix(p, 'a/');
      continue;
    }
    const m = HUNK.exec(line);
    if (m) {
      hunk = {
        oldStart: Number(m[1]),
        oldLines: m[2] === undefined ? 1 : Number(m[2]),
        newStart: Number(m[3]),
        newLines: m[4] === undefined ? 1 : Number(m[4]),
        lines: [],
      };
      current.hunks.push(hunk);
      continue;
    }
    if (hunk && (line.startsWith(' ') || line.startsWith('+') || line.startsWith('-'))) hunk.lines.push(line);
    else if (hunk && line === '') hunk.lines.push(' ');
    else if (line.startsWith('\\ No newline')) continue;
  }
  return patches.filter((p) => p.filePath.length > 0);
}
