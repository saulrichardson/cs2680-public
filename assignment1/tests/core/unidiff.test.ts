import { describe, expect, it } from 'vitest';
import { parseUnifiedDiff, unquoteGitPath } from '@/core/unidiff';

const sample = `diff --git a/README.md b/README.md
index 1234567..89abcde 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # demo
-old line
+new line
+another
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+print(1)
+print(2)
`;

describe('parseUnifiedDiff', () => {
  it('parses files and hunks', () => {
    const patches = parseUnifiedDiff(sample);
    expect(patches.map((p) => p.filePath)).toEqual(['README.md', 'src/new.py']);
    expect(patches[0]?.hunks[0]).toEqual({
      oldStart: 1,
      oldLines: 2,
      newStart: 1,
      newLines: 3,
      lines: [' # demo', '-old line', '+new line', '+another'],
    });
    expect(patches[1]?.hunks[0]?.lines).toEqual(['+print(1)', '+print(2)']);
  });
  it('returns [] for empty input', () => {
    expect(parseUnifiedDiff('')).toEqual([]);
  });
  it('treats a genuinely blank line inside a hunk as a blank context line, without leaking into the next file', () => {
    const withBlankLine = `diff --git a/a.txt b/a.txt
index 1111111..2222222 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,4 @@
 line1

 line2
+line3
diff --git a/b.txt b/b.txt
index 3333333..4444444 100644
--- a/b.txt
+++ b/b.txt
@@ -1,1 +1,1 @@
-old
+new
`;
    const patches = parseUnifiedDiff(withBlankLine);
    expect(patches.map((p) => p.filePath)).toEqual(['a.txt', 'b.txt']);
    expect(patches[0]?.hunks[0]?.lines).toEqual([' line1', ' ', ' line2', '+line3']);
    expect(patches[1]?.hunks[0]?.lines).toEqual(['-old', '+new']);
  });
  it('preserves a genuine blank line when it is the very last line of the input', () => {
    const endsWithBlank = `diff --git a/c.txt b/c.txt
index 5555555..6666666 100644
--- a/c.txt
+++ b/c.txt
@@ -1,2 +1,2 @@
 x

`;
    const patches = parseUnifiedDiff(endsWithBlank);
    expect(patches).toHaveLength(1);
    expect(patches[0]?.hunks[0]?.lines).toEqual([' x', ' ']);
  });
});

describe('unquoteGitPath', () => {
  it('leaves an ordinary path alone', () => {
    expect(unquoteGitPath('src/app.py')).toBe('src/app.py');
    expect(unquoteGitPath('a file with spaces.txt')).toBe('a file with spaces.txt');
  });

  it('undoes the escapes git uses for names it cannot write plainly', () => {
    expect(unquoteGitPath('"we\\tird.txt"')).toBe('we\tird.txt');
    expect(unquoteGitPath('"line\\nbreak.txt"')).toBe('line\nbreak.txt');
    expect(unquoteGitPath('"say \\"hi\\".txt"')).toBe('say "hi".txt');
    expect(unquoteGitPath('"back\\\\slash.txt"')).toBe('back\\slash.txt');
  });

  it('decodes octal escapes as bytes of the name, not as characters', () => {
    // "naïve" is five characters but six bytes; git writes the two non-ASCII bytes separately
    expect(unquoteGitPath('"na\\303\\257ve.txt"')).toBe('naïve.txt');
  });
});

describe('parseUnifiedDiff file names', () => {
  it('reports the real name for a quoted header, so patches match their FileStat', () => {
    const patches = parseUnifiedDiff(
      [
        'diff --git "a/we\\tird.txt" "b/we\\tird.txt"',
        '--- "a/we\\tird.txt"',
        '+++ "b/we\\tird.txt"',
        '@@ -1 +1 @@',
        '-a',
        '+b',
      ].join('\n'),
    );
    expect(patches.map((p) => p.filePath)).toEqual(['we\tird.txt']);
  });

  it('drops the separator tab git adds after an unquoted name containing a space', () => {
    const patches = parseUnifiedDiff(
      [
        'diff --git a/a => b.txt b/a => b.txt',
        '--- a/a => b.txt\t',
        '+++ b/a => b.txt\t',
        '@@ -1 +1 @@',
        '-a',
        '+b',
      ].join('\n'),
    );
    expect(patches.map((p) => p.filePath)).toEqual(['a => b.txt']);
  });
});

describe('hunk bodies take precedence over file headers', () => {
  /**
   * A line whose *content* starts `++ ` reads as `+++ ` in a diff. Checking for file headers first
   * made such a line disappear from the hunk and silently rename the patch that followed it — a
   * valid added line, lost, with no sign that anything had gone wrong.
   */
  it('keeps an added line that looks like a +++ header', () => {
    const patches = parseUnifiedDiff(
      [
        'diff --git a/notes.md b/notes.md',
        '--- a/notes.md',
        '+++ b/notes.md',
        '@@ -1,1 +1,4 @@',
        ' existing',
        '+++ b/not-a-header.txt',
        '+--- a/also-not-a-header.txt',
        '+normal added line',
      ].join('\n'),
    );
    expect(patches).toHaveLength(1);
    expect(patches[0]?.filePath).toBe('notes.md');
    expect(patches[0]?.hunks[0]?.lines).toEqual([
      ' existing',
      '+++ b/not-a-header.txt',
      '+--- a/also-not-a-header.txt',
      '+normal added line',
    ]);
  });

  it('still starts a new file when the next diff header arrives', () => {
    const patches = parseUnifiedDiff(
      [
        'diff --git a/one.txt b/one.txt',
        '--- a/one.txt',
        '+++ b/one.txt',
        '@@ -1 +1 @@',
        '-a',
        '+++ still content',
        'diff --git a/two.txt b/two.txt',
        '--- a/two.txt',
        '+++ b/two.txt',
        '@@ -1 +1 @@',
        '-c',
        '+d',
      ].join('\n'),
    );
    expect(patches.map((p) => p.filePath)).toEqual(['one.txt', 'two.txt']);
    expect(patches[0]?.hunks[0]?.lines).toEqual(['-a', '+++ still content']);
    expect(patches[1]?.hunks[0]?.lines).toEqual(['-c', '+d']);
  });

  it('starts a new hunk on @@ even though it begins with a content prefix', () => {
    const patches = parseUnifiedDiff(
      [
        'diff --git a/x.txt b/x.txt',
        '--- a/x.txt',
        '+++ b/x.txt',
        '@@ -1,1 +1,1 @@',
        '-a',
        '@@ -10,1 +10,1 @@',
        '+b',
      ].join('\n'),
    );
    expect(patches[0]?.hunks).toHaveLength(2);
    expect(patches[0]?.hunks[1]?.oldStart).toBe(10);
  });

  it('does not strip a prefix a real directory happens to share', () => {
    // Compare asks git for `a/` and `b/` explicitly, so a header without one names a file whose
    // own path starts that way.
    const patches = parseUnifiedDiff(
      [
        'diff --git a/b/inside.txt b/b/inside.txt',
        '--- a/b/inside.txt',
        '+++ b/b/inside.txt',
        '@@ -1 +1 @@',
        '-x',
        '+y',
      ].join('\n'),
    );
    expect(patches.map((p) => p.filePath)).toEqual(['b/inside.txt']);
  });
});

describe('unquoteGitPath with characters outside the basic plane', () => {
  /**
   * Decoding character by character handed `TextEncoder` the halves of a surrogate pair
   * separately, so an emoji became two replacement characters. It is a real case: with
   * `core.quotePath=false` the name is written literally, and a tab in the same name still forces
   * the whole thing to be quoted.
   */
  it('keeps an emoji whole in a name that also needs escaping', () => {
    expect(unquoteGitPath('"b/😀\\tname.txt"')).toBe('b/😀\tname.txt');
    expect(unquoteGitPath('"b/naïve\\tname.txt"')).toBe('b/naïve\tname.txt');
    expect(unquoteGitPath('"🎯\\n🎈.txt"')).toBe('🎯\n🎈.txt');
  });

  it('still decodes the octal form of the same characters', () => {
    // what git writes for the same name with `core.quotePath` left on
    expect(unquoteGitPath('"b/\\360\\237\\230\\200\\tname.txt"')).toBe('b/😀\tname.txt');
  });
});
