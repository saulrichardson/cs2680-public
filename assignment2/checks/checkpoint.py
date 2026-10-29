"""Ordinary account effects versus staged context adoption, without model dispatch."""
import hashlib
import json
import tempfile
from pathlib import Path
from mads.agent import Agent
from mads.store import dump


class CompilerRoute:
    config = {'model':'literal-fixture'}

    @staticmethod
    def request_options():
        return {}


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo = root/'repo'
        repo.mkdir()
        a = Agent(repo, 'ORIGINAL TASK is authoritative', root/'run', CompilerRoute(),
                  input_limit=32000, total_limit=40000, output_tokens=4000,
                  mask_at=0, pressure_at=31000)
        a.system = 'literal compiler fixture'
        a.context.working.write_text('old diagnosis')
        a.compile()

        def batch(calls):
            message = {'role':'assistant','tool_calls':[
                {'id':f'{len(a.store.events)}-{i}', 'type':'function',
                 'function':{'name':name,'arguments':dump(args)}}
                for i,(name,args) in enumerate(calls)]}
            record = a.store.add('fixture',blob=a.store.blob(dump(message)))
            return a.batch(message,record)

        def compile(group,restart):
            request = a.compile(group,restart)
            tail = request['messages'][-1]
            facts = (json.loads(tail['content'])['harness_runtime'] if tail['role']=='tool' else
                     json.loads(tail['content'].split('\n',1)[1].removesuffix('</tool_response>')))
            manifest = a.store.record(a.context.context_id)
            assert a.store.verify(manifest['request_blob']).read_text() == dump(request)
            assert a.context.tokens(request) <= a.context.limit
            assert request['messages'][1]['content'] == a.task
            return facts

        old_hash = hashlib.sha256(a.context.working.read_bytes()).hexdigest()
        group,restart = batch([('context_restart',dict(account='revised diagnosis',
                              expected_account_sha256=old_hash,keep_records=[]))])
        result = group['results'][0]
        assert result['effects']=='file_replaced'
        assert result['account_update']['before_sha256']==old_hash
        facts = compile(group,restart)
        assert facts['working_account']['text']=='revised diagnosis'
        assert group['results'][0]['context_change']=='applied'
        assert len(a.context.groups)==1

        stable = a.context.working.read_text()
        artifact = a.store.artifact(a.store.blob('evidence'))
        record = a.store.evidence(artifact,kind='fixture')
        for args in [dict(account='must not write',expected_account_sha256=old_hash),
                     dict(expected_account_sha256=old_hash),
                     dict(account='must not write',keep_records=[{'record_id':record,'view':{'bytes':[0,100]}}])]:
            group,restart = batch([('context_restart',args),('write',dict(path='sibling',content='valid'))])
            assert restart is None and group['results'][0]['status']=='rejected'
            assert group['results'][0]['effects']=='none'
            assert a.context.working.read_text()==stable and (repo/'sibling').read_text()=='valid'
            compile(group,restart)

        group,restart = batch([('context_restart',dict(account='first accepted')),
                               ('context_restart',dict(account='duplicate must not write'))])
        assert group['results'][1]['status']=='rejected' and group['results'][1]['effects']=='none'
        assert a.context.working.read_text()=='first accepted'
        compile(group,restart)

        previous = [g['id'] for g in a.context.groups]
        group,restart = batch([('context_restart',dict(account='file effect survives fit rejection',keep_records=[])),
                               ('context_configure',dict(output_tokens=39999)),
                               ('write',dict(path='another',content='one successful effect'))])
        facts = compile(group,restart)
        assert group['results'][0]['context_change']=='unchanged'
        assert group['results'][0]['effects']=='file_replaced'
        assert a.context.output_tokens==4000 and [g['id'] for g in a.context.groups][:-1]==previous
        assert facts['working_account']['text']=='file effect survives fit rejection'
        assert (repo/'another').read_text()=='one successful effect'
        group,restart = batch([('context_restart',dict(keep_records=[]))])
        compile(group,restart)
        assert group['results'][0]['effects']=='none' and group['results'][0]['context_change']=='applied'

        group,restart = batch([('context_restart',dict(account='superseded')),
                               ('write',dict(path=str(a.context.working),content='later sibling account'))])
        written_hash = group['results'][0]['account_update']['after_sha256']
        facts = compile(group,restart)
        assert facts['working_account']['text']=='later sibling account'
        assert written_hash != hashlib.sha256(a.context.working.read_bytes()).hexdigest()
        assert Path(group['results'][0]['applied_context']['account_read']['path']).read_text()=='later sibling account'

        # Purpose is carried as fallible data without changing the selected bytes.
        purpose = 'Reducer interface for the next edit; NOT a verified requirement'
        selection = dict(record_id=record, part='payload', view={'bytes':[0,8]}, purpose=purpose)
        group,restart = batch([('context_restart',dict(keep_records=[selection]))])
        compile(group,restart)
        packet = json.loads(a.context.evidence([selection],a.context.limit)[0]['content']
                            .split('\n',1)[1].removesuffix('</tool_response>'))
        assert ''.join(packet['text_parts']) == 'evidence'
        assert packet['retention_purpose'] == dict(text=purpose,authorship='agent',validation='unverified')
        assert a.context.context_status()['retained_evidence'][0]['purpose'] == purpose
        assert group['results'][0]['applied_context']['retained_evidence'][0]['purpose'] == purpose
        group,restart = batch([('context_restart',{})])
        compile(group,restart)
        assert a.context.records == [selection]
        group,restart = batch([('context_restart',dict(account='not written',
                              keep_records=[{**selection,'purpose':''}]))])
        assert restart is None and group['results'][0]['status']=='rejected'
        assert a.context.working.read_text()=='later sibling account'
        compile(group,restart)
        group,restart = batch([('context_restart',dict(keep_records=[]))])
        compile(group,restart)
        assert a.context.context_status()['retained_evidence']==[]

        (repo/'search.txt').write_text('valid text')
        group,restart = batch([('search',dict(path='search.txt',query='[',regex=True)),
                               ('write',dict(path='after-regex-error',content='ran'))])
        assert group['results'][0]['status']=='rejected' and group['results'][0]['effects']=='none'
        assert (repo/'after-regex-error').read_text()=='ran'
        compile(group,restart)
        a.tools.jobs.close()
        print('PASS: guarded checkpoint, prevalidation, duplicate, unchanged context with persistent account, recovery without rewrite, sibling supersession, regex rejection, finalized request')


if __name__=='__main__':
    main()
