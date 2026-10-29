"""Recorded Chat Completions and Responses transports with counted request bodies."""
import json
import os
import time
import math
from email.utils import parsedate_to_datetime
from datetime import timezone

import httpx
from openai import OpenAI, APIConnectionError
from .store import EvidenceError
from . import responses

from .routes import ROUTES, route


class Provider:
    """One synchronous API exchange at a time, explicitly linked to its journal record."""

    def __init__(self, store, config=None, timeout=120, retries=2, transport=None, options=None):
        self.store, self.config = store, dict(config or route())
        if retries is not None and (type(retries) is not int or retries < 0):
            raise ValueError('transport retries must be a nonnegative integer or None (unlimited)')
        if timeout is not None and timeout <= 0:
            raise ValueError('transport inactivity timeout must be positive or None')
        self.retries = retries
        self.config['transport'] = dict(inactivity_seconds=timeout,additional_attempts=retries,
            connect_seconds=min(15,timeout) if timeout else None,
            write_seconds=min(30,timeout) if timeout else None,
            pool_seconds=min(15,timeout) if timeout else None)
        timeout = httpx.Timeout(timeout, connect=min(15,timeout), write=min(30,timeout), pool=min(15,timeout)) if timeout else None
        self._options_override = options
        self._active = None
        self._client = OpenAI(api_key=os.environ[self.config['key_env']],
            base_url=self.config['base_url'], timeout=timeout, max_retries=0,
            http_client=httpx.Client(timeout=timeout, transport=transport,
                event_hooks={'request': [self._sent], 'response': [self._received]}))

    def _sent(self, request):
        matches = json.loads(request.content) == self._active['body']
        request.extensions['mads_event'] = self.store.add('http_request',
            **self._active['metadata'], method=request.method, url=str(request.url),
            content_type=request.headers.get('content-type'), blob=self.store.blob(request.content),
            matches_compiled_request=matches)
        if not matches:
            raise ValueError('SDK request differs from the recorded logical request; request not sent')

    def _received(self, response):
        # Read and persist even non-2xx bodies before the SDK can raise an API error.
        raw = response.read()
        self.last_response_record = self.store.add('response', **self._active['metadata'],
            http_request_record=response.request.extensions['mads_event'],
            status=response.status_code, content_type=response.headers.get('content-type'),
            provider_request_id=response.headers.get('x-request-id'),
            retry_after=response.headers.get('retry-after'),
            elapsed_seconds=time.monotonic() - self._active['started'],
            blob=self.store.blob(raw))

    def request_options(self):
        """Route fields included in the compiler's counted logical HTTP body."""
        if self._options_override is not None:
            return __import__('copy').deepcopy(self._options_override)
        if self.config.get('protocol') == 'responses':
            return {'store': False, 'service_tier': 'default',
                    'reasoning': {'effort': self.config.get('reasoning','medium'), 'context': 'all_turns', 'mode': 'standard'},
                    'include': ['reasoning.encrypted_content']}
        if self.config.get('upstream'):
            return {'provider': {'order': [self.config['upstream']],
                                'allow_fallbacks': False, 'require_parameters': True}}
        return {}

    def fork(self, store):
        """A separate evidence stream with this route's actual native options.

        No history, credentials or HTTP client state is copied into model context.
        The new client reads the same configured environment credential as usual.
        """
        fork = self.store.add('provider_fork', child_journal=os.path.relpath(store.root, self.store.root))
        store.add('provider_parent', parent_journal=os.path.relpath(self.store.root, store.root),
                  fork_record=fork)
        return Provider(store, config=self.config,
                        timeout=self.config['transport']['inactivity_seconds'],
                        retries=self.retries, options=self.request_options())

    def prepare_request(self, request):
        return responses.request_body(request) if self.config.get('protocol') == 'responses' else request

    def complete(self, request, context_id=None, **metadata):
        if self._active is not None:
            raise RuntimeError('Provider is synchronous; an API exchange is already active')
        request = {**request, **self.request_options()}
        request_record = self.store.request(request, context_id, **metadata)
        self.last_request_record = request_record
        self.last_response_record = None
        self._active = {'metadata': {'request_record': request_record, **metadata},
                        'started': time.monotonic(), 'body': request}
        try:
            sdk_request = dict(request)
            if 'provider' in sdk_request:
                sdk_request['extra_body'] = {'provider': sdk_request.pop('provider')}
            endpoint = self._client.responses if self.config.get('protocol') == 'responses' else self._client.chat.completions
            attempt = 1
            while True:
                self.last_response_record = None
                self._active['started'] = time.monotonic()
                self._active['metadata'] = {'request_record':request_record, **metadata, 'attempt':attempt}
                attempt_record = self.store.add('api_attempt', **self._active['metadata'])
                self._active['metadata']['attempt_record'] = attempt_record
                try:
                    raw = endpoint.with_raw_response.create(**sdk_request)
                    response = json.loads(raw.text)
                    if not isinstance(response, dict):
                        raise ValueError('Provider returned a nonobject response')
                    if self.config.get('protocol') != 'responses' and not response.get('choices'):
                        raise ValueError('Provider returned no choices')
                    return response
                except BaseException as exc:
                    retryable = isinstance(exc, Exception) and self.transient(exc)
                    retry = retryable and (self.retries is None or attempt <= self.retries)
                    self.store.add('api_error', **self._active['metadata'],
                        type=type(exc).__name__, message=str(exc),
                        response_record=self.last_response_record,
                        retryable=retryable, retry_scheduled=retry,
                        provider_execution='unknown; connection failure or retry does not establish exactly-once generation',
                        elapsed_seconds=time.monotonic()-self._active['started'])
                    if not retry:
                        raise
                    delay, source = self.retry_delay(exc, attempt)
                    self.store.add('api_retry', **self._active['metadata'], delay_seconds=delay,
                                   delay_source=source, next_attempt=attempt+1, tools_replayed=False)
                    time.sleep(delay)
                    attempt += 1
        finally:
            self._active = None

    @staticmethod
    def transient(exc):
        # Never hide a failed evidence write inside the SDK's connection wrapper.
        cause = exc
        seen = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, (EvidenceError, KeyboardInterrupt, ValueError)):
                return False
            cause = cause.__cause__ or cause.__context__
        body = getattr(exc, 'body', None)
        text = json.dumps(body).lower() if body is not None else str(exc).lower()
        if any(term in text for term in ('quota','billing','insufficient_credit','insufficient_funds','payment_required',
                                         'budget_exhausted','budget_exceeded')) or (
                'budget' in text and any(term in text for term in ('exhaust', 'exceed', 'deplet', 'spent'))):
            return False
        status = getattr(exc, 'status_code', None)
        if status is not None:
            temporary_rate_limit = any(term in text for term in (
                'rate_limit_exceeded', 'queue_timeout', 'rate limit', 'rate-limit', 'too many requests'))
            response = getattr(exc, 'response', None)
            retry_after = response is not None and 'retry-after' in response.headers
            return status in (408,500,502,503,504) or status == 429 and (temporary_rate_limit or retry_after)
        if isinstance(exc, APIConnectionError):
            # Require a recognized transport cause, not an arbitrary hook failure.
            return isinstance(exc.__cause__, (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError))
        return False

    @staticmethod
    def retry_delay(exc, attempt):
        """Honor explicit provider delays; otherwise back off without a total-time gate.

        HTTP Retry-After may be seconds or an HTTP date. Some proxy providers
        expose the upstream delay only in structured error metadata. Only these
        fields are used, never numbers guessed from free-form error messages.
        """
        response = getattr(exc, 'response', None)
        candidates = []
        if response is not None:
            candidates.append((response.headers.get('retry-after'), 'http_retry_after'))
        body = getattr(exc, 'body', None)
        if isinstance(body, dict):
            error = body.get('error', body)
            if isinstance(error, dict):
                metadata = error.get('metadata', {})
                for fields, label in ((error, 'error'), (metadata, 'metadata')):
                    if isinstance(fields, dict):
                        candidates.append((fields.get('retry_after_seconds'), label+'_retry_after_seconds'))
                        headers = fields.get('headers', {})
                        if isinstance(headers, dict):
                            candidates.extend((v, label+'_retry_after') for k, v in headers.items()
                                              if k.lower() == 'retry-after')
        for value, source in candidates:
            if value is None or isinstance(value, bool):
                continue
            try:
                seconds = float(value)
            except (ValueError, TypeError, OverflowError):
                if not isinstance(value, str):
                    continue
                try:
                    date = parsedate_to_datetime(value)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    seconds = max(0, date.timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    continue
            if math.isfinite(seconds) and seconds >= 0:
                return seconds, source
        # Cap only each fallback sleep, not attempts or cumulative task duration.
        return min(2**min(max(attempt-1, 0), 6), 60), 'exponential_backoff'

    @staticmethod
    def context_rejection(exc):
        body = getattr(exc, "body", None)
        text = json.dumps(body).lower() if body is not None else str(exc).lower()
        return getattr(exc, "status_code", None) in (400, 413) and any(term in text for term in ("context_length", "context length", "maximum context", "too many tokens"))

    @staticmethod
    def context_limit(exc):
        # Only explicit structured token limits are trusted; free-form messages
        # and ambiguous "limit" numbers may refer to rates or output tokens.
        body = getattr(exc, 'body', None)
        if not isinstance(body, dict):
            return None
        fields = body.get('error', body)
        if not isinstance(fields, dict):
            return None
        values = [fields[k] for k in ('max_context_tokens', 'max_context_length')
                  if type(fields.get(k)) is int and fields[k] > 0]
        return min(values) if values else None

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
