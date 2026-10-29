"""Real file and failure-boundary checks for the dedicated patch capability."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch as mock

from src.teamharness import patching
from src.teamharness.tools import ToolRuntime, TOOLS


# Original malformed input from the small v5 capability fixture, not a benchmark
# repair. Preserve its actual syntax so the test does not normalize it first.
V5_MALFORMED_PATCH = '''*** Begin Patch ***
*** Update File: parser_fixture.py ***
--- a/parser_fixture.py
+++ b/parser_fixture.py
@@ -1,5 +1,14 @@
 import json
\x20
 def parse_results(text):
-    document = json.loads(text)
-    return [item["name"] for item in document["results"]]
+    document = json.loads(text)
+    if not isinstance(document, dict):
+        return []
+    results = document.get("results")
+    if not isinstance(results, list):
+        return []
+    out = []
+    for item in results:
+        if isinstance(item, dict) and "name" in item:
+            name = item["name"]
+            if isinstance(name, str) and len(name) > 0:
+                out.append(name)
+    return out
 *** End Patch ***'''


def patch(*lines):
    return '\n'.join(('*** Begin Patch', *lines, '*** End Patch', ''))


class PatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / 'repo'
        self.workspace.mkdir()
        self.evidence = root / 'evidence'

    def apply(self, text, operation='one'):
        return patching.apply_patch(self.workspace, self.evidence, text, operation)

    def put(self, path, content, mode=0o644):
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode)
        return target

    def test_multifile_add_update_delete_saves_exact_before_and_after(self):
        source = self.put('run.sh', b'#!/bin/bash\necho before\n', 0o751)
        removed = self.put('old.txt', b'old bytes\x00')
        result = self.apply(patch('*** Update File: run.sh', '@@', ' #!/bin/bash', '-echo before', '+echo after',
            '*** Add File: nested/new.txt', '+first', '+second', '*** Delete File: old.txt'))
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'#!/bin/bash\necho after\n')
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o751)
        self.assertEqual((self.workspace / 'nested/new.txt').read_bytes(), b'first\nsecond\n')
        self.assertFalse(removed.exists())
        self.assertEqual([row['status'] for row in result['files']], ['applied'] * 3)
        for row in result['files']:
            for phase in ('before', 'after'):
                receipt = row[phase]
                if receipt['exists']:
                    raw = Path(receipt['snapshot']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt['sha256'])
                    self.assertEqual(len(raw), receipt['bytes'])
            self.assertEqual(patching._identity(row['after']), patching._identity(row['planned_after']))
        self.assertEqual(json.loads(Path(result['receipt_path']).read_text()), result)

    def test_raw_v5_rejection_then_explicit_canonical_patch_uses_real_receipts(self):
        self.put('parser_fixture.py', b'import json\n\ndef parse_results(text):\n    document = json.loads(text)\n    return [item["name"] for item in document["results"]]\n')
        self.put('example.txt', b'context\nold value\n')
        self.put('obsolete.txt', b'obsolete bytes\n')
        original = {p.name: p.read_bytes() for p in self.workspace.iterdir()}
        agent = Path(self.temp.name) / 'agent'
        agent.mkdir()
        runtime = ToolRuntime(None, 'a' * 32,
            {'root': str(agent), 'workspace': str(self.workspace), 'base': None, 'tools': TOOLS})
        rejected = runtime('apply_patch', {'patch': V5_MALFORMED_PATCH}, 'raw-v5-attempt')
        self.assertFalse(rejected['applied'])
        self.assertIs(rejected['source_writes_started'], False)
        self.assertEqual(rejected['state'], 'conflict')
        self.assertEqual({p.name: p.read_bytes() for p in self.workspace.iterdir()}, original)
        self.assertEqual(Path(rejected['patch_path']).read_text(), V5_MALFORMED_PATCH)
        self.assertEqual(rejected['patch_sha256'], hashlib.sha256(V5_MALFORMED_PATCH.encode()).hexdigest())
        self.assertEqual(json.loads(Path(rejected['receipt_path']).read_text()), rejected)
        advertised = next(tool['function'] for tool in TOOLS if tool['function']['name'] == 'apply_patch')
        self.assertIn(rejected['example']['patch'], advertised['description'])
        # This is a separate explicit call using the returned valid example,
        # whose stated source preconditions were created above. No repair occurs
        # inside the rejected operation or its replay.
        self.assertEqual(runtime('apply_patch', {'patch': V5_MALFORMED_PATCH}, 'raw-v5-attempt'), rejected)
        applied = runtime('apply_patch', rejected['example'], 'explicit-corrected-example')
        self.assertTrue(applied['applied'])
        self.assertEqual((self.workspace / 'notes.txt').read_bytes(), b'new note\n\n')
        self.assertEqual((self.workspace / 'example.txt').read_bytes(), b'context\nnew value\n')
        self.assertFalse((self.workspace / 'obsolete.txt').exists())
        self.assertEqual((self.workspace / 'parser_fixture.py').read_bytes(), original['parser_fixture.py'])
        self.assertFalse((self.workspace / 'workspace').exists())
        self.assertEqual([row['operation'] for row in applied['files']], ['add', 'update', 'delete'])
        for row in applied['files']:
            self.assertEqual(row['status'], 'applied')
            for phase in ('before', 'after'):
                receipt = row[phase]
                if receipt['exists']:
                    raw = Path(receipt['snapshot']).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt['sha256'])
                    self.assertEqual(len(raw), receipt['bytes'])

    def test_add_and_git_diff_syntax_errors_get_reference_without_source_changes(self):
        source = self.put('example.txt', b'old value\n')
        malformed = [patch('*** Add File: added.txt', 'unprefixed content'),
                     patch('*** Update File: example.txt', '--- a/example.txt', '+++ b/example.txt',
                           '@@ -1 +1 @@', '-old value', '+new value'),
                     patch('*** Update File: example.txt', '@@', '-old value', '+new value').replace('*** End Patch', '*** End Patch ***')]
        for index, text in enumerate(malformed):
            with self.subTest(index=index):
                rejected = self.apply(text, str(index))
                self.assertFalse(rejected['applied'])
                self.assertIs(rejected['source_writes_started'], False)
                self.assertEqual(rejected['example']['patch'], patching.PATCH_EXAMPLE)
                self.assertEqual(source.read_bytes(), b'old value\n')
                self.assertFalse((self.workspace / 'added.txt').exists())

    def test_unusual_file_header_path_remains_literal(self):
        result = self.apply(patch('*** Add File: literal ***', '+original bytes'))
        self.assertTrue(result['applied'])
        self.assertEqual((self.workspace / 'literal ***').read_bytes(), b'original bytes\n')
        self.assertFalse((self.workspace / 'literal').exists())

    def test_conflicting_second_file_prevents_all_source_writes(self):
        first = self.put('first', b'before\n')
        second = self.put('second', b'actual\n')
        result = self.apply(patch('*** Update File: first', '@@', '-before', '+after',
            '*** Update File: second', '@@', '-stale', '+after'))
        self.assertFalse(result['applied'])
        self.assertEqual(result['state'], 'conflict')
        self.assertEqual(first.read_bytes(), b'before\n')
        self.assertEqual(second.read_bytes(), b'actual\n')
        self.assertEqual(result['files'][0]['status'], 'not_attempted')
        self.assertFalse(list(Path(result['archive']).glob('*.after')))

    def test_second_write_failure_retains_first_and_does_not_replay(self):
        for name in ('a', 'b', 'c'):
            self.put(name, b'before\n')
        text = patch(*(line for name in ('a', 'b', 'c') for line in
            (f'*** Update File: {name}', '@@', '-before', '+after')))
        commit = patching._commit_file
        calls = []
        def fail_second(path, raw, mode):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError('injected second write failure')
            commit(path, raw, mode)
        with mock.object(patching, '_commit_file', fail_second):
            result = self.apply(text)
        self.assertEqual(result['state'], 'partial')
        self.assertFalse(result['applied'])
        self.assertIsNot(result.get('source_writes_started'), False)
        self.assertNotIn('example', result)
        self.assertEqual([row['status'] for row in result['files']], ['applied', 'failed_unchanged', 'not_attempted'])
        self.assertEqual([(self.workspace / name).read_bytes() for name in ('a', 'b', 'c')], [b'after\n', b'before\n', b'before\n'])
        self.assertEqual(self.apply(text), result)
        self.assertEqual(calls, ['a', 'b'])

    def test_duplicate_and_invalid_hunks_never_write(self):
        source = self.put('a', b' x\nx\nx\n')
        cases = [
            patch('*** Delete File: a', '*** Add File: a', '+other'),
            patch('*** Update File: a', '@@', '-x', '+y'),  # Ambiguous exact text.
            patch('*** Update File: a', '@@', '- x ', '+y'),  # No trimming/fuzzy search.
            patch('*** Update File: a', '@@', ' x'),  # No change.
            patch('*** Update File: a', '@@', '+y'),  # No positional evidence.
            patch('*** Update File: a', '@@', '- x', '+y', '@@', '- x', '+y'),
            patch('*** Update File: a', '@@', 'unprefixed'),
            patch('*** Update File: a', '@@', '- x', '+y', '*** Move to: b'),
            patch('*** Update File: a', '@@', '- x', '+y', '\\ No newline at end of file'),
        ]
        for index, text in enumerate(cases):
            with self.subTest(index=index):
                result = self.apply(text, str(index))
                self.assertEqual(result['state'], 'conflict')
                self.assertEqual(source.read_bytes(), b' x\nx\nx\n')

    def test_incompatible_parent_child_paths_conflict_before_creation(self):
        result = self.apply(patch('*** Add File: parent/child', '+child', '*** Add File: parent', '+parent'))
        self.assertEqual(result['state'], 'conflict')
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_line_endings_final_newline_and_executable_mode_preserved(self):
        source = self.put('script', b'first\r\nsecond\r\nlast', 0o755)
        result = self.apply(patch('*** Update File: script', '@@', '-second', '+replacement'))
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'first\r\nreplacement\r\nlast')
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o755)
        result = self.apply(patch('*** Update File: script', '@@', '+appended', '*** End of File'), 'append')
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'first\r\nreplacement\r\nlast\r\nappended')

    def test_explicit_newline_markers_remove_and_add_final_newline(self):
        source = self.put('a', b'old\n')
        result = self.apply(patch('*** Update File: a', '@@', '-old', '+new', '\\ No newline at end of file'))
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'new')
        result = self.apply(patch('*** Update File: a', '@@', '-new', '\\ No newline at end of file', '+next'), 'add-newline')
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'next\n')
        result = self.apply(patch('*** Update File: a', '@@', '-next', '\\ No newline at end of file', '+wrong'), 'wrong-marker')
        self.assertEqual(result['state'], 'conflict')
        self.assertEqual(source.read_bytes(), b'next\n')

    def test_add_empty_unicode_and_explicit_unterminated_file(self):
        result = self.apply(patch('*** Add File: empty', '*** Add File: unicode', '+café\u2028literal',
            '\\ No newline at end of file'))
        self.assertTrue(result['applied'])
        self.assertEqual((self.workspace / 'empty').read_bytes(), b'')
        self.assertEqual((self.workspace / 'unicode').read_text(), 'café\u2028literal')
        result = self.apply(patch('*** Update File: empty', '@@', '+hello'), 'empty-insertion')
        self.assertTrue(result['applied'])
        self.assertEqual((self.workspace / 'empty').read_bytes(), b'hello\n')

    def test_relative_paths_only_and_symlinks_are_explicit_capability_errors(self):
        outside = Path(self.temp.name) / 'outside'
        outside.write_bytes(b'outside\n')
        (self.workspace / 'link').symlink_to(outside)
        (self.workspace / 'linked-dir').symlink_to(outside.parent, target_is_directory=True)
        names = (str(outside), '../outside', './outside', 'a/../outside', 'link', 'linked-dir/outside', 'C:\\outside', '')
        for index, name in enumerate(names):
            result = self.apply(patch(f'*** Update File: {name}', '@@', '-outside', '+changed'), str(index))
            self.assertFalse(result['applied'])
            self.assertEqual(result['state'], 'conflict')
            self.assertEqual(outside.read_bytes(), b'outside\n')
        with self.assertRaisesRegex(ValueError, 'outside'):
            patching.apply_patch(self.workspace, self.workspace / 'evidence', patch('*** Add File: x', '+x'), 'x')

    def test_exact_section_anchor_and_end_of_file_disambiguate(self):
        source = self.put('a', b'first\nvalue\nsecond\nvalue\n')
        result = self.apply(patch('*** Update File: a', '@@ second', '-value', '+changed'))
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'first\nvalue\nsecond\nchanged\n')
        result = self.apply(patch('*** Update File: a', '@@', '-changed', '+last', '*** End of File'), 'eof')
        self.assertTrue(result['applied'])
        self.assertEqual(source.read_bytes(), b'first\nvalue\nsecond\nlast\n')

    def test_shell_race_is_detected_before_later_file_not_rolled_back(self):
        first = self.put('a', b'before\n')
        second = self.put('b', b'before\n')
        commit = patching._commit_file
        def outside_writer(path, raw, mode):
            commit(path, raw, mode)
            second.write_bytes(b'concurrent shell bytes\n')
        with mock.object(patching, '_commit_file', outside_writer):
            result = self.apply(patch('*** Update File: a', '@@', '-before', '+after',
                '*** Update File: b', '@@', '-before', '+after'))
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['files'][1]['status'], 'conflict')
        self.assertEqual(first.read_bytes(), b'after\n')
        self.assertEqual(second.read_bytes(), b'concurrent shell bytes\n')

    def test_completed_write_then_exception_is_reported_as_observed_applied(self):
        source = self.put('a', b'before\n')
        commit = patching._commit_file
        def write_then_error(path, raw, mode):
            commit(path, raw, mode)
            raise OSError('receipt boundary failed after replacement')
        with mock.object(patching, '_commit_file', write_then_error):
            result = self.apply(patch('*** Update File: a', '@@', '-before', '+after'))
        self.assertFalse(result['applied'])
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['files'][0]['status'], 'applied_observed_after_error')
        self.assertEqual(source.read_bytes(), b'after\n')

    def test_receipt_write_error_does_not_relabel_applied_source_as_unexecuted(self):
        source = self.put('a', b'before\n')
        save = patching._save
        failed = []
        def fail_once(path, value):
            if path.name == '0000.receipt.json' and value['status'] == 'applied' and not failed:
                failed.append(True)
                raise OSError('injected receipt failure after source write')
            save(path, value)
        with mock.object(patching, '_save', fail_once):
            result = self.apply(patch('*** Update File: a', '@@', '-before', '+after'))
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['files'][0]['status'], 'applied_observed_after_error')
        self.assertEqual(source.read_bytes(), b'after\n')

    def test_add_does_not_overwrite_file_that_appears_at_commit_boundary(self):
        commit = patching._commit_file
        def race(path, raw, mode, **kwargs):
            path.write_bytes(b'concurrent writer')
            commit(path, raw, mode, **kwargs)
        with mock.object(patching, '_commit_file', race):
            result = self.apply(patch('*** Add File: a', '+patch text'))
        self.assertFalse(result['applied'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual((self.workspace / 'a').read_bytes(), b'concurrent writer')
        self.assertFalse(list(self.workspace.glob('.team-patch-*')))

    def test_incomplete_intent_is_never_replayed_and_reuse_conflicts(self):
        text = patch('*** Add File: a', '+one')
        result = self.apply(text)
        self.assertEqual(self.apply(patch('*** Add File: a', '+two'))['state'], 'identity_conflict')
        Path(result['receipt_path']).unlink()
        (self.workspace / 'a').unlink()
        replay = self.apply(text)
        self.assertEqual(replay['state'], 'unknown')
        self.assertFalse(replay['replay_safe'])
        self.assertFalse((self.workspace / 'a').exists())

    def test_workspace_lock_serializes_dedicated_patch_calls(self):
        source = self.put('a', b'initial\n')
        entered, release = threading.Event(), threading.Event()
        commit = patching._commit_file
        receipts = {}
        def slow_commit(path, raw, mode):
            if raw == b'first\n':
                entered.set()
                if not release.wait(5):
                    raise OSError('test release did not arrive')
            commit(path, raw, mode)
        def apply(name, before, after):
            receipts[name] = self.apply(patch('*** Update File: a', '@@', '-'+before, '+'+after), name)
        with mock.object(patching, '_commit_file', slow_commit):
            one = threading.Thread(target=apply, args=('first', 'initial', 'first'))
            two = threading.Thread(target=apply, args=('second', 'first', 'second'))
            one.start()
            self.assertTrue(entered.wait(5))
            two.start()
            time.sleep(.05)
            self.assertNotIn('second', receipts)
            release.set()
            one.join(5)
            two.join(5)
        self.assertFalse(one.is_alive() or two.is_alive())
        self.assertTrue(all(row['applied'] for row in receipts.values()))
        self.assertEqual(source.read_bytes(), b'second\n')


if __name__ == '__main__':
    unittest.main()
