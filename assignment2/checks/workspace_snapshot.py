"""Snapshot provenance, safe links, and refusal before evaluation."""
from pathlib import Path
import os
import tempfile
from unittest.mock import patch
from experiments.snapshot_workspace import inventory, snapshot


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root/'source'; source.mkdir()
        (source/'sub').mkdir(); (source/'empty').mkdir()
        file = source/'sub'/'binary'; file.write_bytes(b'\xff\x00\r\n'); file.chmod(0o751)
        (source/'link').symlink_to('sub/binary')
        result = snapshot(source, root/'copy')
        assert result['tree_sha256'] == inventory(root/'copy')['tree_sha256']
        assert (root/'copy'/'link').is_symlink() and (root/'copy'/'empty').is_dir()
        for destination in [root/'copy', source/'nested']:
            try: snapshot(source,destination)
            except ValueError: pass
            else: raise AssertionError('unsafe destination accepted')
        (source/'escape').symlink_to('/tmp')
        try: snapshot(source,root/'unsafe')
        except ValueError: pass
        else: raise AssertionError('escaping link accepted')
        assert not (root/'unsafe').exists()
        (source/'escape').unlink()
        import shutil
        copytree = shutil.copytree
        def mutate(*args,**kwargs):
            result = copytree(*args,**kwargs)
            file.write_bytes(b'mutated')
            return result
        with patch('experiments.snapshot_workspace.shutil.copytree', side_effect=mutate):
            try: snapshot(source,root/'drift')
            except ValueError: pass
            else: raise AssertionError('source drift accepted')
        assert not (root/'drift').exists() and not list(root.glob('.snapshot-*'))
        os.mkfifo(source/'pipe')
        try: snapshot(source,root/'fifo')
        except ValueError: pass
        else: raise AssertionError('FIFO accepted')
        print('PASS: byte/mode/link/empty directory identity; existing/nested/escaping/special refusal; drift detection and cleanup')

if __name__ == '__main__': main()
