"""Literal response reconstruction through production Agent; no live provider claim.

Usage: python -m checks.completion_boundaries FRESH_ROOT RECORDED_RESPONSE_BLOB
The recorded body must contain a whitespace-only final response from a real run.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

from mads.agent import Agent, replay_message
from mads.store import Store, dump


def response(message, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'native_finish_reason': finish,
                         'message': message}]}


def call(name, arguments, identity):
    return {'id': identity, 'type': 'function',
            'function': {'name': name, 'arguments': dump(arguments)}}


class LiteralResponses:
    """A labeled sequence adapter, never a network or simulated integration claim."""
    config = {'provider': 'literal_reconstruction', 'model': 'recorded-response-fixture'}

    def __init__(self, store, responses):
        self.store, self.responses, self.requests = store, responses, []

    def request_options(self):
        return {}

    @staticmethod
    def context_rejection(error):
        return False

    def complete(self, request, context_id, **metadata):
        index = len(self.requests)
        assert index < len(self.responses), 'Unexpected additional fixture request'
        self.requests.append(copy.deepcopy(request))
        self.last_request_record = self.store.request(request, context_id, **metadata)
        body = copy.deepcopy(self.responses[index])
        self.last_response_record = self.store.add('literal_provider_response',
            request_record=self.last_request_record, blob=self.store.blob(dump(body)),
            provider_integration=False)
        return body


def main(root, recorded_response):
    root, recorded_response = Path(root).resolve(), Path(recorded_response).resolve()
    root.mkdir(parents=True, exist_ok=False)
    raw = recorded_response.read_bytes()
    recorded = json.loads(raw)
    native = recorded['choices'][0]['message']
    assert native['content'] and not native['content'].strip()
    assert recorded['choices'][0]['finish_reason'] == 'stop' and not native.get('tool_calls')
    (root/'provenance.json').write_text(dump({
        'recorded_response': str(recorded_response),
        'recorded_response_sha256': hashlib.sha256(raw).hexdigest(),
        'agent_sha256': hashlib.sha256(Path(sys.modules[Agent.__module__].__file__).read_bytes()).hexdigest(),
        'provider_integration': False,
        'scope': 'Literal reconstruction using a preserved real response and real production tools'})+'\n')
    checked = []

    def run(name, bodies, steps=0):
        directory = root/name
        repo = directory/'repo'
        repo.mkdir(parents=True)
        store = Store(directory/'run/journal')
        provider = LiteralResponses(store, bodies)
        result = Agent(repo, 'Completion boundary fixture', directory/'run', provider,
                       store, steps=steps).run()
        return result, provider, store, repo

    exact_claim = ' \nCompleted the fixture.\t '
    final = response({'role': 'assistant', 'tool_calls': [
        call('finish', {'summary': exact_claim}, 'final')]}, 'tool_calls')
    write = response({'role': 'assistant', 'content': None, 'tool_calls': [
        call('write', {'path': 'effect.txt', 'content': 'COMMITTED_ONCE'}, 'once')]}, 'tool_calls')
    commentary = response({'role': 'assistant', 'content': ' \nThe first write succeeded. I will inspect it next.\t ',
                           'reasoning_content': 'Intermediate explanation; another action remains.'})
    inspect = response({'role': 'assistant', 'tool_calls': [
        call('read', {'path': 'effect.txt', 'range': 'full'}, 'inspect')]}, 'tool_calls')
    result, provider, store, repo = run('commentary-after-effect', [write, recorded, commentary, inspect, final])
    assert result['iterations'] == 5 and result['agent_outcome'] == {'status': 'completed', 'claim': exact_claim}
    assert (repo/'effect.txt').read_text() == 'COMMITTED_ONCE'
    assert [e['tool_name'] for e in store.events if e['event'] == 'invocation'] == ['write', 'read', 'finish']
    replay, _ = replay_message(native)
    assert replay in provider.requests[2]['messages']
    assistant = [e for e in store.events if e['event'] == 'assistant'][1]
    assert json.loads((store.blobs/assistant['blob']).read_text()) == native
    assert commentary['choices'][0]['message'] in provider.requests[3]['messages']
    assert any(message.get('role') == 'tool' and 'COMMITTED_ONCE' in message.get('content', '')
               for message in provider.requests[4]['messages'])
    checked.append('recorded-whitespace-continues-with-exact-native-fields-and-no-effect-replay')
    checked.append('substantive-commentary-stays-exact-and-next-action-runs')
    checked.append('explicit-finish-preserves-exact-padded-claim')

    for name, fields in [('empty', {'content': ''}), ('absent', {}), ('null', {'content': None})]:
        result, provider, store, _ = run(name, [response({'role': 'assistant', **fields}), final])
        assert result['iterations'] == 2 and result['agent_outcome']['claim'] == exact_claim
        assert [e['tool_name'] for e in store.events if e['event'] == 'invocation'] == ['finish']
        checked.append(name+'-content-continues')

    result, _, store, _ = run('operator-bound-after-whitespace', [recorded], steps=1)
    assert result['harness_outcome']['status'] == 'operator_turn_limit'
    assert result['agent_outcome']['status'] == 'not_completed'
    assert not [e for e in store.events if e['event'] == 'invocation']
    checked.append('whitespace-alone-is-not-a-completion-claim')

    result, _, store, _ = run('operator-bound-after-confident-text',
        [response({'role': 'assistant', 'content': 'All work is complete and all tests passed.'})], steps=1)
    assert result['harness_outcome']['status'] == 'operator_turn_limit'
    assert result['agent_outcome']['status'] == 'not_completed'
    checked.append('confident-text-does-not-imply-completion')

    explicit_claim = ' \n '
    finish = response({'role': 'assistant', 'tool_calls': [
        call('context_configure', {'keep_recent': 0}, 'configure'),
        call('finish', {'summary': explicit_claim}, 'finish'),
        call('write', {'path': 'skipped.txt', 'content': 'MUST_NOT_EXIST'}, 'skipped')]}, 'tool_calls')
    result, _, store, repo = run('explicit-finish-unchanged', [finish])
    assert result['iterations'] == 1 and result['agent_outcome']['claim'] == explicit_claim
    assert result['harness_outcome']['status'] == 'completed'
    assert not (repo/'skipped.txt').exists()
    outcomes = [e['result'] for e in store.events if e['event'] == 'tool_result']
    assert outcomes[0]['context_change'] == 'pending'
    assert outcomes[2]['status'] == 'skipped' and outcomes[2]['effects'] == 'none'
    exchange = next(e for e in store.events if e['event'] == 'exchange')
    assert json.loads(exchange['messages'][1]['content'])['context_change'] == 'not_applied'
    assert not [e for e in store.events if e['event'] == 'context_configuration']
    checked.append('explicit-finish-unchanged-and-later-effects-skipped')
    checked.append('pending-context-change-not-applied-after-finish')
    (root/'coverage.json').write_text(json.dumps({'verified': checked, 'provider_integration': False}, indent=2)+'\n')
    print(json.dumps({'verified': checked, 'provider_integration': False}))


if __name__ == '__main__':
    main(*sys.argv[1:])
