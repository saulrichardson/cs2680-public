"""Core file tools use real paths and retain originals; no Git or provider required."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from src.bridge import Workspace

class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'files'
        self.repo.mkdir()
        self.ws = Workspace(str(self.repo),str(self.root/'state'),capture_patch=False)

    def call(self, op, **args):
        return self.ws.effect(op,dict(workspace=self.ws.facts(),**args))

    def test_read_preserves_binary_and_line_endings_with_byte_pagination(self):
        raw = b'one\r\n'+('héllo\n'.encode())+b'\xff\x00tail'
        path = self.repo/'data'
        path.write_bytes(raw)
        result = self.call('read_file',path='data',start_line=2,line_count=1,max_output_bytes=3)
        self.assertEqual(result['offset'],5)
        self.assertFalse(result['range_complete'])
        self.assertEqual(result['total_lines'],3)
        self.assertEqual(Path(result['_artifacts']['snapshot_ref']['path']).read_bytes(),raw)
        rest = self.call('read_file',path='data',offset=result['next_offset'])
        self.assertEqual(rest['next_offset'],len(raw))
        self.assertTrue(rest['complete'])

    def test_edits_are_all_or_nothing_and_do_not_hide_stale_source(self):
        path = self.repo/'script'
        original = b'x\r\nx\r\n'
        path.write_bytes(original)
        path.chmod(0o755)
        ambiguous = self.call('edit_file',path='script',operation='replace',replacements=[{'old':'x','new':'y'}])
        self.assertFalse(ambiguous['applied'])
        self.assertEqual(ambiguous['match_count'],2)
        self.assertEqual(path.read_bytes(),original)
        stale = self.call('edit_file',path='script',operation='write',content='bad',expected_sha256='0'*64)
        self.assertFalse(stale['applied'])
        result = self.call('edit_file',path='script',operation='replace',
            replacements=[{'old':'x','new':'y','count':2}])
        self.assertTrue(result['applied'])
        self.assertEqual(path.read_bytes(),b'y\r\ny\r\n')
        self.assertEqual(path.stat().st_mode & 0o777,0o755)
        self.assertEqual(Path(result['_artifacts']['before_ref']['path']).read_bytes(),original)
        self.assertEqual(result['after_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())

    def test_create_delete_absolute_paths_symlinks_and_missing_parents(self):
        path = self.root/'outside'/'new.txt'
        result = self.call('edit_file',path=str(path),operation='create',content='hello',create_parents=True)
        self.assertTrue(result['applied'])
        duplicate = self.call('edit_file',path=str(path),operation='create',content='clobber')
        self.assertFalse(duplicate['applied'])
        link = self.repo/'linked'
        link.symlink_to(path)
        changed = self.call('edit_file',path='linked',operation='write',content='updated')
        self.assertTrue(changed['applied'])
        self.assertTrue(link.is_symlink())
        self.assertEqual(path.read_text(),'updated')
        removed = self.call('edit_file',path=str(path),operation='delete')
        self.assertTrue(removed['applied'])
        self.assertFalse(path.exists())
        self.assertEqual(Path(removed['_artifacts']['before_ref']['path']).read_text(),'updated')
        missing = self.call('edit_file',path='missing/child',operation='create',content='no')
        self.assertFalse(missing['applied'])
        self.assertFalse((self.repo/'missing').exists())

    def test_large_write_has_no_command_argument_size_ceiling(self):
        content = 'source line\n' * 20000
        result = self.call('edit_file',path='large.txt',operation='write',content=content)
        self.assertTrue(result['applied'],result)
        self.assertEqual((self.repo/'large.txt').read_text(),content)

    def test_search_reports_scope_and_keeps_all_results(self):
        (self.repo/'a.py').write_text('Needle\nneedle\n')
        (self.repo/'.hidden').write_text('needle\n')
        (self.repo/'binary').write_bytes(b'needle\0')
        (self.repo/'.git').mkdir()
        (self.repo/'.git/config').write_text('needle\n')
        (self.repo/'loop').symlink_to(self.repo, target_is_directory=True)
        result = self.call('search_files',query='needle',ignore_case=True,max_output_bytes=5)
        self.assertEqual(result['matches'],3)
        self.assertEqual(result['skipped_binary_files'],1)
        self.assertFalse(result['complete'])
        full = Path(result['_artifacts']['results_ref']['path']).read_text()
        self.assertEqual(len(full.splitlines()),3)
        self.assertIn('.hidden',full)
        self.assertNotIn('/.git/',full)
        included = self.call('search_files',query='needle',exclude=[])
        self.assertEqual(included['matches'],3) # two text files + .git, case-sensitive
        files = self.call('search_files',glob='*.py')
        self.assertEqual(files['matches'],1)
        regex = self.call('search_files',query='^N.*le$',regex=True,glob='*.py')
        self.assertEqual(regex['matches'],1)
        (self.repo/'broken').symlink_to(self.repo/'missing')
        errors = self.call('search_files',query='needle')
        self.assertFalse(errors['scan_complete'])
        self.assertTrue(errors['errors'])

if __name__ == '__main__':
    unittest.main()
