import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mads.store import Store, present
from mads.tools import Tools


class UnchangedEditsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'journal')
        self.tools = Tools(self.root, self.root/'run', self.store)
        self.addCleanup(self.tools.jobs.close)
        self.file = self.root/'example'
        self.file.write_bytes(b'alpha\r\nbeta\n')

    def test_identical_edit_and_write_do_not_replace_file(self):
        inode = self.file.stat().st_ino
        before = self.file.read_bytes()
        real_replace = os.replace
        def guarded_replace(source, destination):
            self.assertNotEqual(Path(destination), self.file)
            return real_replace(source, destination)
        for operation in (lambda: self.tools.edit('example', 'alpha', 'alpha'),
                          lambda: self.tools.write('example', before.decode())):
            with patch('mads.tools.os.replace', side_effect=guarded_replace):
                result = operation()
            self.assertEqual(self.tools.effects, 'none')
            self.assertFalse(result['changed'])
            self.assertEqual(result['before_sha256'], result['after_sha256'])
            self.assertEqual(self.file.stat().st_ino, inode)
            self.assertEqual(self.file.read_bytes(), before)
            rendered = present(result['output'], 12000)
            self.assertIn('No change', ''.join(rendered['text_parts']))
            self.assertEqual(self.store.events[-1]['kind'], 'file_unchanged')

    def test_guards_matches_symlinks_creation_and_real_change(self):
        with self.assertRaisesRegex(ValueError, 'version mismatch'):
            self.tools.edit('example', 'alpha', 'alpha', expected_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'matched 0'):
            self.tools.edit('example', 'absent', 'absent')
        self.tools.write('example', 'a a')
        with self.assertRaisesRegex(ValueError, 'matched 2'):
            self.tools.edit('example', 'a', 'a')
        self.assertFalse(self.tools.edit('example', 'a', 'a', replace_all=True)['changed'])
        (self.root/'link').symlink_to(self.file)
        self.assertFalse(self.tools.write('link', 'a a')['changed'])
        self.tools.write('empty', '')
        self.assertTrue((self.root/'empty').exists())
        self.assertEqual(self.tools.effects, 'file_replaced')
        changed = self.tools.edit('example', 'a a', 'b b')
        self.assertNotEqual(changed['before_sha256'], changed['after_sha256'])
        self.assertEqual(self.tools.effects, 'file_replaced')
