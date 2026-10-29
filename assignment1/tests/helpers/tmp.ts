import { execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * `realpath`, because macOS puts the temp directory behind a symlink and the server canonicalises
 * every folder it is given (`projectDir`). Without it a test's own path and the one the server
 * records are two spellings of the same directory, and every comparison between them fails.
 */
export function makeTmpDir(prefix = 'mc-test-'): { path: string; cleanup: () => void } {
  const path = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  return { path, cleanup: () => rmSync(path, { recursive: true, force: true }) };
}

/** A tiny git repo with one commit containing README.md and src/app.py. */
export function makeTmpGitRepo(): { path: string; head: string; cleanup: () => void } {
  const dir = makeTmpDir('mc-repo-');
  const git = (...args: string[]) =>
    execFileSync('git', ['-C', dir.path, ...args], { encoding: 'utf8' }).trim();
  git('init', '-q', '-b', 'main');
  git('config', 'user.email', 'test@example.com');
  git('config', 'user.name', 'Test');
  writeFileSync(join(dir.path, 'README.md'), '# demo\n');
  execFileSync('mkdir', ['-p', join(dir.path, 'src')]);
  writeFileSync(join(dir.path, 'src', 'app.py'), 'def main():\n    return 1\n');
  git('add', '-A');
  git('commit', '-q', '-m', 'init');
  return { path: dir.path, head: git('rev-parse', 'HEAD'), cleanup: dir.cleanup };
}
