import json
from pathlib import Path
import tempfile
import unittest

from mads.handoff import export_bundle, render_bundle, validate_bundle
from mads.store import Store


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.store = Store(self.run / 'journal')
        self.id = self.store.add('evidence', blob=self.store.blob('old\ninterface(é)\nnext\n'))
        self.task = self.root / 'task'
        self.account = self.root / 'account'
        self.task.write_text('ORIGINAL requirement\n')
        self.account.write_text('Fallible claim, not checked.\n')
        self.dest = self.root / 'bundle'

    def export(self, selections=None):
        return export_bundle(self.run, self.dest, self.task, self.account,
                             selections if selections is not None else [{'record_id': self.id, 'view': {'lines': [2, 2]}}])

    def test_exact_and_explicit_transfer(self):
        before = self.store.path.read_bytes()
        m = self.export()
        self.assertEqual((self.dest / 'evidence-0000.txt').read_bytes(), 'interface(é)\n'.encode())
        self.assertEqual(m['evidence'][0]['selected_byte_range'], [4, 18])
        self.assertEqual(self.task.read_bytes(), (self.dest / 'original-task.txt').read_bytes())
        self.assertEqual(before, self.store.path.read_bytes())
        text = render_bundle(self.dest)
        self.assertIn('Source record IDs are foreign IDs', text)
        self.assertIn('interface(é)', text)
        self.assertNotIn('old\\n', text)
        self.assertEqual(text, (self.dest / 'presentation.md').read_text())
        self.assertEqual(validate_bundle(self.dest), m)

    def test_render_preserves_crlf_bytes(self):
        self.task.write_bytes(b'ORIGINAL\r\nrequirement\r\n')
        self.account.write_bytes(b'Claim\r\n')
        record = self.store.add('evidence', blob=self.store.blob(b'Exact\r\ninterface\r\n'))
        self.export([{'record_id': record}])
        text = render_bundle(self.dest)
        self.assertEqual((self.dest / 'account.md').read_bytes(), b'Claim\r\n')
        evidence = [json.loads(line)['historical_text'] for line in text.splitlines()
                    if line.startswith('{"historical_text":')]
        self.assertEqual(evidence, ['Exact\r\ninterface\r\n'])
        self.assertEqual((self.dest / 'presentation.md').read_bytes(), text.encode('utf-8'))
        self.assertEqual((self.dest / 'original-task.txt').read_bytes(), self.task.read_bytes())

    def test_partial_journal_tail_is_disclosed(self):
        with self.store.path.open('ab') as out:
            out.write(b'{"id":')
        m = self.export()
        self.assertEqual(m['source']['incomplete_tail_bytes'], 6)
        self.assertEqual(m['source']['journal_head'], 1)

    def test_record_part_and_empty_selections(self):
        m = self.export([{'record_id': 1, 'part': 'record', 'purpose': 'inspect provenance'}])
        self.assertEqual(json.loads((self.dest/'evidence-0000.txt').read_text())['id'], 1)
        self.assertEqual(m['evidence'][0]['purpose'], 'inspect provenance')
        self.dest = self.root / 'empty'
        self.assertEqual(self.export([])['evidence'], [])

    def test_bad_selector_leaves_no_export(self):
        for selection in [{'record_id': 99}, {'record_id': True},
                          {'record_id': 1, 'view': {'bytes': [0, 1000]}},
                          {'record_id': 1, 'view': {'bytes': [5, 5]}},
                          {'record_id': 1, 'view': {'bytes': [14, 15]}},
                          {'record_id': 1, 'part': 'wrong'}]:
            with self.subTest(selection=selection), self.assertRaises((ValueError, UnicodeError)):
                self.export([selection])
            self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob('.handoff-*')), [])

    def test_no_overwrite(self):
        self.export()
        with self.assertRaises(ValueError):
            self.export()

    def test_tamper_and_unsafe_member_rejected(self):
        self.export()
        (self.dest/'account.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            render_bundle(self.dest)
        self.dest = self.root / 'other'
        self.export()
        manifest = json.loads((self.dest/'manifest.json').read_text())
        manifest['account']['file'] = '../account'
        (self.dest/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            validate_bundle(self.dest)

    def test_corrupt_source_rejected(self):
        (self.store.blobs/self.store.record(1)['blob']).write_text('bad')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.export()
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()
