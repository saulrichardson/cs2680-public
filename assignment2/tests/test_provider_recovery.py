"""Transport recovery through the real SDK/Agent; no live keys or model calls."""
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import RateLimitError

from mads.agent import Agent, transport_retries
from mads.provider import Provider
from mads.store import Store

CONFIG = dict(provider='course', base_url='http://fixture.invalid/v1',
              key_env='MADS_TEST_KEY', model='fixture')
# Shape observed in an actual upstream response; no request/user identifiers.
QUEUE = {'error': {'message': 'Provider returned error', 'code': 429,
                  'metadata': {'provider_error_code': 'queue_timeout',
                               'retry_after_seconds': 1,
                               'headers': {'Retry-After': '1'}}}}


def error(body=None, headers=None):
    response = httpx.Response(429, headers=headers, request=httpx.Request('POST', 'http://fixture.invalid'))
    return RateLimitError('fixture', response=response, body=body)


def answer(name, arguments):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'tool_calls': [{'id': 'call-'+name, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}]}


class RetryClassificationTests(unittest.TestCase):
    def test_explicit_temporary_limits_and_terminal_budgets(self):
        for exc in (error(QUEUE), error({'message': 'Too many requests'}),
                    error({'message': 'temporarily rate-limited upstream'}),
                    error({}, {'Retry-After': '10'})):
            with self.subTest(body=exc.body):
                self.assertTrue(Provider.transient(exc))
        for body in ({}, {'error': {'code': 'insufficient_quota'}},
                     {'error': {'message': 'Monthly budget exceeded'}},
                     {'error': {'message': 'Budget for Assignment2 exhausted',
                                'metadata': {'retry_after_seconds': 1}}},
                     {'error': {'code': 'billing_error', 'message': 'rate limit exceeded'}}):
            with self.subTest(body=body):
                # A timing header never overrides an explicit permanent budget error.
                self.assertFalse(Provider.transient(error(body, {'Retry-After': '1'}))
                                 if body else Provider.transient(error(body)))

    def test_retry_after_sources_and_date_do_not_guess_message_numbers(self):
        self.assertEqual(Provider.retry_delay(error(QUEUE), 2), (1, 'metadata_retry_after_seconds'))
        self.assertEqual(Provider.retry_delay(error(QUEUE, {'Retry-After': '125'}), 1),
                         (125, 'http_retry_after'))
        now = 1700000000
        date = format_datetime(datetime.fromtimestamp(now+91, timezone.utc), usegmt=True)
        with patch('mads.provider.time.time', return_value=now):
            self.assertEqual(Provider.retry_delay(error({}, {'Retry-After': date}), 1),
                             (91, 'http_retry_after'))
        for value in ('nonsense', 'NaN', 'inf', '-1'):
            self.assertEqual(Provider.retry_delay(error({'message': 'retry after 999 seconds'},
                                                       {'Retry-After': value}), 3),
                             (4, 'exponential_backoff'))
        self.assertEqual(Provider.retry_delay(error({}), 1000000), (60, 'exponential_backoff'))
        body = {'error': {'metadata': {'headers': {'retry-after': '3.5'}}}}
        self.assertEqual(Provider.retry_delay(error(body), 1), (3.5, 'metadata_retry_after'))
        # OpenAI SDK may expose already-unwrapped error objects.
        self.assertEqual(Provider.retry_delay(error(QUEUE['error']), 1),
                         (1, 'metadata_retry_after_seconds'))

    def test_retry_configuration_and_cli_have_no_arbitrary_three_attempt_gate(self):
        for value, expected in [('0', 0), ('12', 12), ('unlimited', None), ('none', None)]:
            self.assertEqual(transport_retries(value), expected)
        for value in ('-1', '1.5', 'forever?'):
            with self.assertRaises(Exception):
                transport_retries(value)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, MADS_TEST_KEY='local-only'):
            for value in (0, 12, None):
                with Provider(Store(Path(directory)/str(value)), config=CONFIG, retries=value) as provider:
                    self.assertEqual(provider.config['transport']['additional_attempts'], value)
            for value in (-1, 1.5, True, 'unlimited'):
                with self.assertRaises(ValueError):
                    Provider(Store(Path(directory)/('invalid'+str(value))), config=CONFIG, retries=value)


class AgentRetryTests(unittest.TestCase):
    def test_queue_recovery_keeps_attempt_evidence_and_does_not_repeat_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root/'repo'; repo.mkdir()
            store = Store(root/'run/journal'); requests = []
            def handle(request):
                requests.append(bytes(request.content))
                if len(requests) == 1:
                    return httpx.Response(200, json=answer('bash', {'command': 'printf x >> effect.txt'}))
                # More than the former three-attempt ceiling, then success.
                if len(requests) < 7:
                    return httpx.Response(429, json=QUEUE)
                return httpx.Response(200, json=answer('finish', {'summary': 'recovered'}))
            sleep = Mock()
            clock = SimpleNamespace(sleep=sleep, time=time.time, monotonic=time.monotonic)
            # Replace only Provider's clock, not the shared time module used by Jobs.
            with patch.dict(os.environ, MADS_TEST_KEY='local-only'), patch('mads.provider.time', clock):
                with Provider(store, config=CONFIG, retries=None, transport=httpx.MockTransport(handle)) as provider:
                    agent = Agent(repo, 'Append once, then finish.', root/'run', provider, store, session_role='reviewer')
                    result = agent.run()
            self.assertEqual(result['harness_outcome']['status'], 'completed')
            self.assertEqual((repo/'effect.txt').read_text(), 'x')
            self.assertEqual(len(requests), 7)
            self.assertTrue(all(body == requests[1] for body in requests[1:]))
            self.assertEqual(sleep.call_count, 5)
            self.assertEqual([c.args for c in sleep.call_args_list], [(1,)]*5)
            self.assertEqual(len([e for e in store.events if e['event'] == 'job_started']), 1)
            self.assertEqual(len([e for e in store.events if e['event'] == 'invocation']), 2)
            retries = [e for e in store.events if e['event'] == 'api_retry']
            self.assertEqual(len({e['request_record'] for e in retries}), 1)
            self.assertEqual([e['attempt'] for e in retries], [1, 2, 3, 4, 5])
            self.assertTrue(all(e['tools_replayed'] is False for e in retries))
            self.assertTrue(all(e['matches_compiled_request'] for e in store.events if e['event'] == 'http_request'))
            from mads.inspection.audit_journal import audit
            audited = audit(store.root, strict=True)
            self.assertTrue(audited['passed'], audited['errors'])

    def test_unlimited_policy_still_stops_on_explicit_budget_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, MADS_TEST_KEY='local-only'):
            store = Store(Path(directory)/'journal'); calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(429, json={'error': {'message': 'Budget for Assignment2 exhausted'}})
            with patch('mads.provider.time.sleep') as sleep:
                with Provider(store, config=CONFIG, retries=None, transport=httpx.MockTransport(handle)) as provider:
                    with self.assertRaises(RateLimitError):
                        provider.complete({'model': 'fixture', 'messages': []})
                sleep.assert_not_called()
            self.assertEqual(len(calls), 1)
            decision = next(e for e in store.events if e['event'] == 'api_error')
            self.assertFalse(decision['retryable']); self.assertFalse(decision['retry_scheduled'])


if __name__ == '__main__':
    unittest.main()
