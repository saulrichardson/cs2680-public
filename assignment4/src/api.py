"""HTTP effects only. Runtime adapters own request and response meaning."""
import http.client
import json
import os
import ssl
from pathlib import Path
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from .store import Store


class InferenceStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Inference stopped: ' + reason)


class Provider:
    def __init__(self, root, logger=None):
        self.root = Path(root)
        self.logger = logger
        self.calls = 0
        self.model = os.environ.get('A4_MODEL') or os.environ.get('CS2680_MODEL_STANDARD')
        self.url = os.environ.get('A4_BASE_URL') or os.environ.get('CS2680_BASE_URL')
        self.key = os.environ.get('A4_API_KEY') or os.environ.get('CS2680_API_KEY')
        self.protocol = os.environ.get('A4_API_PROTOCOL') or ('openai_responses' if self.url and urllib.parse.urlsplit(self.url).hostname == 'api.openai.com' else 'chat_completions')
        if self.protocol not in ('chat_completions','openai_responses'):
            raise ValueError('Unknown A4_API_PROTOCOL')
        self.path = '/responses' if self.protocol == 'openai_responses' else '/chat/completions'
        parsed = urllib.parse.urlsplit(self.url or '')
        public_url = urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path.rstrip('/'), '', ''))
        if parsed.port:
            public_url = urllib.parse.urlunsplit((parsed.scheme, (parsed.hostname or '')+':'+str(parsed.port), parsed.path.rstrip('/'), '', ''))
        self.route = {'protocol':self.protocol,'endpoint':public_url,'model':self.model}
        if not all((self.model, self.url, self.key)):
            raise ValueError('Set A4_MODEL/A4_BASE_URL/A4_API_KEY or course API environment variables')
        self.store = Store(self.root / 'context.sqlite3')
        self.calls = self.store.db.execute("SELECT COUNT(*) FROM objects WHERE kind='transport_request'").fetchone()[0]

    def call(self, payload, *, deadline=None, cancelled=None, retry_response=None):
        request = payload['request']
        selected_model = request.get('model')
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise ValueError('Request requires a nonempty model')
        route = dict(self.route, model=selected_model)
        logical_request_id = uuid.uuid4().hex
        regeneration_of = None
        attempt = 0
        def check_active():
            if cancelled is not None and cancelled():
                raise InferenceStopped('cancelled')
            if deadline is not None and time.time() >= deadline:
                raise InferenceStopped('deadline')

        def wait(delay):
            # The caller's actual limit also governs transport retries. Checking
            # during backoff does not cancel or replay any model tool effects.
            end = time.monotonic() + delay
            while True:
                check_active()
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return
                time.sleep(min(.1, remaining))

        while True:
            check_active()
            attempt += 1
            self.calls += 1
            ident = self.calls
            start = time.time()
            record = {'event': payload['event'], 'branch': payload['branch'],
                      'request': request, 'route':route, 'started_at': start,
                      'logical_request_id': logical_request_id, 'transport_attempt': attempt}
            if regeneration_of is not None:
                record['regeneration_of'] = regeneration_of
            # Persist the exact body at the transport boundary, without credentials.
            links = [{'relation':'request','ref':payload['request_ref']}] if 'request_ref' in payload else []
            sent = self.store.put('transport_request',payload['branch'],record,links)
            def settle():
                return self.store.put('transport_response',payload['branch'],record,
                                      [{'relation':'transport_request','ref':sent}])
            def interrupted(error):
                partial = getattr(error, 'partial', b'')
                if isinstance(partial, bytes) and partial:
                    record['partial_response_ref'] = self.store.put_blob('raw_response',payload['branch'],partial,
                        metadata={'complete':False,'route':route},links=[{'relation':'transport_request','ref':sent}])
                record.update(error_type=type(error).__name__, usage_state='unknown', elapsed_s=time.time()-start)
                settle()
                if self.logger:
                    self.logger.api_request(ident, None, None, None, selected_model)
            def retry():
                delay = min(30, 2 ** min(attempt, 5))
                if self.logger:
                    self.logger.api_retry(ident, 'Transient inference failure; no action authorized', delay)
                wait(delay)
            request_bytes = json.dumps(request).encode()
            self.store.put_blob('raw_request',payload['branch'],request_bytes, links=[{'relation':'transport_request','ref':sent}])
            req = urllib.request.Request(self.url.rstrip('/') + self.path,
                data=request_bytes, headers={
                    'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key,
                    'X-A4-Request-ID': logical_request_id})
            no_retry = False
            relay_state = None
            try:
                timeout = {} if deadline is None else {'timeout': max(.001, deadline - time.time())}
                with urllib.request.urlopen(req, **timeout) as response:
                    record['status'] = response.status
                    record['request_id'] = response.headers.get('x-request-id')
                    no_retry = response.headers.get('X-A4-No-Retry') == '1'
                    relay_state = response.headers.get('X-A4-Relay-State')
                    no_retry = no_retry or relay_state == 'uncertain'
                    if relay_state is not None:
                        record['relay_state'] = relay_state
                    if no_retry:
                        record['relay_no_retry'] = True
                    raw = response.read()
            except urllib.error.HTTPError as e:
                record.update(status=e.code, request_id=e.headers.get('x-request-id'))
                no_retry = e.headers.get('X-A4-No-Retry') == '1'
                relay_state = e.headers.get('X-A4-Relay-State')
                no_retry = no_retry or relay_state == 'uncertain'
                if relay_state is not None:
                    record['relay_state'] = relay_state
                if no_retry:
                    record['relay_no_retry'] = True
                try:
                    error_bytes = e.read()
                except (OSError, http.client.HTTPException) as error:
                    interrupted(error)
                    if not no_retry and e.code in (408, 500, 502, 503, 504):
                        retry()
                        continue
                    raise
                finally:
                    e.close()
                error_ref = self.store.put_blob('raw_response',payload['branch'],error_bytes,
                    metadata={'status':e.code,'route':route},links=[{'relation':'transport_request','ref':sent}])
                record['raw_response_ref'] = error_ref
                text = error_bytes.decode(errors='replace')
                record.update(status=e.code, error_body=text, elapsed_s=time.time()-start)
                quota_exhausted = any(s in text.lower() for s in ('insufficient_quota', 'insufficient_credits', 'credit balance'))
                if e.code == 429 and not quota_exhausted and not no_retry:
                    record['usage_state'] = 'rejected_before_generation'
                    settle()
                    delay = min(60, 2 ** min(attempt, 6))
                    try:
                        delay = max(delay, float(e.headers.get('Retry-After', 0)))
                    except ValueError:
                        pass
                    if self.logger:
                        self.logger.api_retry(ident, text, delay)
                    wait(delay)
                    continue
                if relay_state != 'uncertain' and e.code in (400, 413) and any(s in text.lower() for s in
                    ('context_length_exceeded', 'maximum context length', 'context window', 'input too long')):
                    record['usage_state'] = 'rejected_before_generation'
                    settle()
                    return {'error_kind': 'context_capacity', 'response': record}
                unknown = relay_state == 'uncertain' or e.code >= 500 or e.code == 408
                record['usage_state'] = 'unknown' if unknown else 'rejected_before_generation'
                settle()
                if self.logger and unknown:
                    self.logger.api_request(ident, None, None, None, selected_model)
                if not no_retry and e.code in (408, 500, 502, 503, 504):
                    retry()
                    continue
                raise RuntimeError(f'Provider HTTP {e.code}; see {self.store.path}, request {sent}') from e
            except (OSError, http.client.HTTPException) as error:
                # Only acquisition/reading is retried. Persistence or decoding
                # failures below are not transport faults and must not regenerate.
                interrupted(error)
                if no_retry or isinstance(getattr(error, 'reason', error), (ssl.SSLCertVerificationError, http.client.InvalidURL)):
                    raise
                retry()
                continue
            except Exception as e:
                record.update(error_type=type(e).__name__, usage_state='unknown', elapsed_s=time.time()-start)
                settle()
                if self.logger:
                    self.logger.api_request(ident, None, None, None, selected_model)
                raise
            try:
                raw_ref = self.store.put_blob('raw_response',payload['branch'],raw,
                    metadata={'status':record['status'],'route':route},
                    links=[{'relation':'transport_request','ref':sent}])
                record['raw_response_ref'] = raw_ref
                body = json.loads(raw)
                if not isinstance(body,dict):
                    raise ValueError('Provider response must be an object; original bytes retained')
            except Exception as e:
                record.update(error_type=type(e).__name__, usage_state='unknown', elapsed_s=time.time()-start)
                settle()
                if self.logger:
                    self.logger.api_request(ident, None, None, None, selected_model)
                raise
            # Routers can deliver an upstream error envelope with HTTP 200.
            # Classify the body before treating it as a model completion.
            error = body.get('error')
            if error is not None:
                text = json.dumps(error, ensure_ascii=False)
                code = error.get('code') if isinstance(error, dict) else None
                try:
                    code = int(code)
                except (TypeError, ValueError):
                    code = None
                capacity = any(s in text.lower() for s in
                    ('context_length_exceeded', 'maximum context length', 'context window', 'input too long'))
                usage = body.get('usage')
                record.update(response=body, error_body=text, provider_error_code=code,
                              elapsed_s=time.time()-start,
                              usage_state='reported' if isinstance(usage, dict) and usage else
                              ('rejected_before_generation' if capacity and relay_state != 'uncertain' else 'unknown'))
                receipt_ref = settle()
                if capacity and relay_state != 'uncertain':
                    return {'error_kind':'context_capacity', 'response':record,
                            'raw_response_ref':raw_ref, 'transport_response_ref':receipt_ref}
                quota = any(s in text.lower() for s in ('insufficient_quota', 'insufficient_credits', 'credit balance'))
                if code in (408, 429, 500, 502, 503, 504) and not quota and not no_retry:
                    retry()
                    continue
                raise RuntimeError(f'Provider error envelope {code}; see {self.store.path}, request {sent}')
            usage = body.get('usage') or {}
            if not isinstance(usage,dict):
                usage = {}
            record.update(response=body, elapsed_s=time.time()-start,
                          usage_state='reported' if usage else 'unknown')
            receipt_ref = settle()
            if self.logger:
                self.logger.api_request(ident, usage.get('prompt_tokens',usage.get('input_tokens')), usage.get('completion_tokens',usage.get('output_tokens')),
                                        usage.get('total_tokens'), selected_model)
            # Response meaning belongs to the adapter. A no-tools generation
            # failure uses this same journal/backoff, with every attempt retained.
            if retry_response is not None and retry_response(body) and not no_retry:
                # The server already completed this failed generation. Reusing
                # its relay identity would fetch the same cached error forever.
                # Lost transport replies above keep their original identity.
                regeneration_of = sent
                logical_request_id = uuid.uuid4().hex
                retry()
                continue
            return {'raw_response':body,'route':route,'raw_response_ref':raw_ref,
                    'transport_response_ref':receipt_ref}
