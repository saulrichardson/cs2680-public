import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from src.teamharness.baseline import freeze, restore, digest


class BaselineArchive(unittest.TestCase):
    def test_restore_preserves_ignored_bytes_links_and_modes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / '.gitignore').write_text('dependency\n')
            (source / 'dependency').write_bytes(b'offline dependency')
            (source / 'script').write_text('original')
            (source / 'script').chmod(0o755)
            outside = root / 'outside'
            outside.write_text('external state')
            (source / 'external-link').symlink_to(outside)
            saved = freeze(source, root / 'baseline.tar')
            (source / 'script').write_text('model mutation')
            recovered = restore(saved, root / 'recovery')
            self.assertEqual((recovered / 'script').read_text(), 'original')
            self.assertEqual((source / 'script').read_text(), 'model mutation')
            self.assertEqual((recovered / 'dependency').read_bytes(), b'offline dependency')
            self.assertEqual((recovered / 'script').stat().st_mode & 0o777, 0o755)
            self.assertEqual((recovered / 'external-link').readlink(), outside)
            self.assertEqual(outside.read_text(), 'external state')
            with self.assertRaises(FileExistsError):
                restore(saved, root / 'recovery')
            with Path(saved['path']).open('ab') as stream:
                stream.write(b'tamper')
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                restore(saved, root / 'other')
            self.assertFalse((root / 'other').exists())

    def test_symlink_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'bad.tar'
            with tarfile.open(path, 'w') as archive:
                directory = tarfile.TarInfo('baseline')
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
                link = tarfile.TarInfo('baseline/link')
                link.type = tarfile.SYMTYPE
                link.linkname = str(root / 'outside')
                archive.addfile(link)
                member = tarfile.TarInfo('baseline/link/file')
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'non-directory'):
                restore({'path': str(path), 'sha256': digest(path)}, root / 'recovery')
            self.assertFalse((root / 'recovery').exists())
            self.assertFalse((root / 'outside').exists())
