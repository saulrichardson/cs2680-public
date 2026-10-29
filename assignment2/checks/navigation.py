"""Snapshot search and optimistic content guards; no model/provider required."""
import hashlib
import json
import tempfile
from pathlib import Path
from mads.store import Store, compact_result
from mads.tools import Tools, validate


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools = Tools(root/'repo', root/'run', Store(root/'journal'))
        tools.repo.mkdir()
        target = tools.repo/'unicode.txt'
        data = 'α\r\nmatch µ\n\nmatch two\nlast\u2028separator\n'.encode()
        target.write_bytes(data)
        def page(**kw):
            result = tools.search(**kw)
            compact = compact_result(result)
            return result, json.loads(Path(result['output']['path']).read_text())
        result, first = page(path='unicode.txt',query='match',max_matches=1,context_lines=1)
        assert result['source_sha256'] == hashlib.sha256(data).hexdigest()
        assert first['line_count'] == 5 and first['scope']['through_line'] == 3
        hit = first['matches'][0]
        assert hit['line'] == 2 and data[slice(*hit['match_byte_range'])] == b'match'
        retained = tools.store.history(hit['retain']['record_id'])
        assert Path(retained['path']).read_bytes()[slice(*hit['snippet_byte_range'])].decode() == hit['text']
        assert retained['artifact_id'] == result['source_sha256']
        target.write_text('changed after first search\n')
        validate('search',first['continuation'])
        second_result, second = page(**first['continuation'])
        assert second['matches'][0]['line'] == 4 and second['continuation'] is None
        assert second['source']['artifact_id'] == first['source']['artifact_id']
        assert second['source']['path'] == first['source']['path']
        assert second_result['source_record'] == result['source_record']
        checked = tools.evidence_check([result['observation_record'],second_result['observation_record']])
        assert [c['status'] for c in json.loads(Path(checked['output']['path']).read_text())['checks']] == ['changed']*2
        # Explicit provenance cannot be attached to unrelated or live paths.
        for bad in [dict(first['continuation'],path='unicode.txt'),
                    dict(first['continuation'],source_record=result['observation_record'])]:
            before = len(tools.store.events)
            try:
                tools.search(**bad)
            except ValueError:
                pass
            else:
                raise AssertionError('Incorrect continuation source accepted')
            assert len(tools.store.events) == before
        _, absent = page(path=first['source']['path'],query='absent')
        assert absent['scope']['exhausted'] and not absent['matches']
        _, regex = page(path=first['source']['path'],query=r'match \w+',regex=True,context_lines=0)
        assert len(regex['matches']) == 2 and regex['matches'][0]['text'] == 'match µ\n'
        target.write_bytes(b'')
        _, empty = page(path='unicode.txt',query='x')
        assert empty['line_count'] == 0 and empty['matches'] == []
        for query, regex in [('',False),('[',True)]:
            try:
                tools.search('unicode.txt',query,regex)
            except (ValueError, __import__('re').error):
                pass
            else:
                raise AssertionError('bad query accepted')
        target.write_bytes(b'\xff')
        try:
            tools.search('unicode.txt','x')
        except UnicodeDecodeError:
            pass
        else:
            raise AssertionError('binary treated as valid UTF-8')
        target.write_bytes(data)
        before = hashlib.sha256(data).hexdigest()
        validate('edit',dict(path='unicode.txt',old='match',new='hit',replace_all=True,expected_sha256=before))
        changed = tools.edit('unicode.txt','match','hit',True,before)
        assert changed['before_sha256'] == before and changed['replacements'] == 2
        assert changed['after_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
        for method, args in [(tools.edit,('unicode.txt','hit','again')), (tools.write,('unicode.txt','lost'))]:
            tools.effects = 'none'
            stable = target.read_bytes()
            try:
                method(*args,expected_sha256=before)
            except ValueError as exc:
                assert 'version mismatch' in str(exc)
            else:
                raise AssertionError('stale write accepted')
            assert tools.effects == 'none' and target.read_bytes() == stable
        target.chmod(0o751)
        tools.write('unicode.txt','updated',expected_sha256=changed['after_sha256'])
        assert target.stat().st_mode & 0o777 == 0o751
        created = tools.write('new/note.txt','created',expected_sha256='missing')
        assert created['created'] and created['before_sha256'] == 'missing'
        try:
            tools.write('missing/dir/file','no',expected_sha256=before)
        except ValueError:
            assert not (tools.repo/'missing').exists()
        else:
            raise AssertionError('guarded missing file accepted')
        validate('search',dict(path='x',query='q',max_matches=1,start_line=1,regex=True))
        print('PASS: immutable Unicode/LF/CRLF search, exact snippets/retention, pagination after mutation, regex/errors, stale guards, missing creation, mode preservation')

if __name__ == '__main__':
    main()
