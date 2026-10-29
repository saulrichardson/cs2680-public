"""Exercise actual tool effects and their next-request delivery, both native protocols."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from mads.inspection.audit_journal import audit
from mads.agent import Agent
from mads.provider import Provider
from mads.store import Store


class WireImprovementTests(unittest.TestCase):
    def test_noop_and_selected_review_reach_next_request(self):
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root/'repo'; repo.mkdir()
                (repo/'source.py').write_text('value = "draft"\n')
                store = Store(root/'run/journal')
                requests = []
                actions = [('edit', {'path': 'source.py', 'old': 'draft', 'new': 'draft'}),
                           ('review', {'paths': ['source.py']}),
                           ('finish', {'summary': 'Fixture, not a correctness claim.'})]

                def handle(request):
                    index = len(requests)
                    requests.append(json.loads(request.content))
                    name, args = actions[index]
                    call = 'fixture-'+str(index)
                    if protocol == 'responses':
                        body = {'id': 'resp-'+call, 'status': 'completed', 'output': [
                            {'type': 'function_call', 'id': 'item-'+call, 'call_id': call,
                             'name': name, 'arguments': json.dumps(args), 'status': 'completed'}]}
                    else:
                        body = {'choices': [{'finish_reason': 'tool_calls', 'message': {
                            'role': 'assistant', 'content': '', 'tool_calls': [
                                {'id': call, 'type': 'function', 'function': {
                                    'name': name, 'arguments': json.dumps(args)}}]}}]}
                    return httpx.Response(200, json=body)

                config = dict(provider='fixture', protocol=protocol,
                              base_url='http://fixture.invalid/v1',
                              key_env='MADS_TEST_KEY', model='fixture')
                with patch.dict(os.environ, {'MADS_TEST_KEY': 'local-fixture-only'}):
                    with Provider(store, config=config, retries=0,
                                  transport=httpx.MockTransport(handle)) as provider:
                        result = Agent(repo, 'Replace draft with final in source.py.',
                                       root/'run', provider, store, session_role='reviewer').run()
                self.assertEqual(result['cleanup_outcome']['status'], 'completed')
                self.assertEqual(len(requests), 3)
                self.assertTrue(all(e['matches_compiled_request'] for e in store.events
                                    if e['event'] == 'http_request'))

                def receipts(body):
                    seq = body.get('messages', body.get('input'))
                    return [json.loads(x.get('content', x.get('output'))) for x in seq
                            if x.get('role') == 'tool' or x.get('type') == 'function_call_output']

                noop = receipts(requests[1])[0]
                self.assertEqual(noop['effects'], 'none')
                self.assertFalse(noop['changed'])
                self.assertIn('No change', json.dumps(noop))
                review = receipts(requests[2])[-1]
                self.assertIn('Replace draft with final in source.py.', json.dumps(review))
                self.assertIn('value = \\"draft\\"', json.dumps(review))
                self.assertEqual(review['scope']['current_file_snapshots'], 1)
                self.assertFalse(review['scope']['task_only'])
                checked = audit(store.root, strict=True)
                self.assertTrue(checked['passed'], checked)


if __name__ == '__main__':
    unittest.main()
