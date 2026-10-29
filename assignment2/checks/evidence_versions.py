"""Historical source bytes stay available; version checks do not decide truth."""
import json
import tempfile
from pathlib import Path
from mads.store import Store
from mads.tools import Tools, validate


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools = Tools(root,root/'run',Store(root/'journal'))
        target = root/'sample.txt'
        target.write_text('α baseline\n')
        original = tools.read(str(target))['observation_record']
        search = tools.search(str(target),'baseline')['observation_record']
        def check(ids):
            result=tools.evidence_check(ids)
            return json.loads(Path(result['output']['path']).read_text())['checks']
        same=check([original,search])
        assert [c['status'] for c in same] == ['same','same']
        target.write_text('corrected interpretation\n')
        changed=check([original,search])
        assert [c['status'] for c in changed] == ['changed','changed']
        assert Path(changed[0]['original_read']['path']).read_text() == 'α baseline\n'
        assert Path(changed[0]['current_read']['path']).read_text() == 'corrected interpretation\n'
        assert check([changed[0]['current_record']])[0]['status'] == 'same'
        # Stored artifact reads deliberately do not guess a live provenance link.
        artifact_read=tools.read(changed[0]['original_read']['path'])['observation_record']
        diff=tools.write(str(target),'new')['observation_record']
        assert [c['status'] for c in check([artifact_read,diff])] == ['no_file_source']*2
        target.unlink()
        assert check([original])[0]['status'] == 'missing'
        target.mkdir()
        assert check([original])[0]['status'] == 'unavailable'
        target.rmdir()
        target.write_bytes(b'\xff\x00binary')
        binary=tools.read(str(target))['observation_record']
        assert check([binary])[0]['status'] == 'same'
        captured=tools.store.snapshot(target)
        captured.update(observation_kind='new_file_snapshot',changed_during_capture=True)
        unstable=tools.store.evidence(captured,kind='file_read')
        assert check([unstable])[0]['status'] == 'unstable'
        before=len(tools.store.events)
        try:
            tools.evidence_check([original,9999999])
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid reference accepted')
        assert len(tools.store.events) == before
        for ids in [[],list(range(1,22))]:
            try:
                validate('evidence_check',dict(record_ids=ids))
            except ValueError:
                pass
            else:
                raise AssertionError('Unbounded records')
        print('PASS: same/changed/missing/non-regular/binary/unstable snapshots; search-source link; no guessed provenance; immutable recovery; validation before observation')

if __name__ == '__main__':
    main()
