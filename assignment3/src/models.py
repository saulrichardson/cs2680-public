"""Complete-response inference, physical limits, and one task spending ledger."""
from __future__ import annotations

from collections import deque

import copy
from dataclasses import dataclass
import hashlib
import http.client
import json
import math
import os
import re
from decimal import Decimal
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .run_control import RunHalted, RunControlError, BudgetExceeded, AllocationExceeded
from .maintenance import capacity_scope


class CapacityError(RuntimeError):
    pass


class ProviderCapacityRejected(CapacityError):
    """Definite pre-generation capacity rejection; the chosen view is unchanged."""
    def __init__(self, detail, *, kind, response_path, supported_output_maximum=None, allocation=None):
        super().__init__(f"Provider rejected {kind} capacity before generation; no tool actions executed. Exact response: {response_path}. {detail}")
        self.kind = kind
        self.detail = detail
        self.response_path = str(response_path)
        self.supported_output_maximum = supported_output_maximum
        self.allocation = allocation


def provider_capacity_rejection(detail, response_path):
    """Interpret only explicit provider capacity terms; retain the full receipt."""
    # Normalize thousands separators only between digits; preserve the exact receipt.
    lowered = re.sub(r"(?<=\d)[,_](?=\d{3}(?:\D|$))", "", detail.lower())
    if not any(term in lowered for term in ('context length', 'context window', 'maximum context',
            'context_length_exceeded', 'requested token count', 'max_tokens', 'max_completion_tokens', 'input tokens', 'output tokens')):
        return None
    # Only a stated numeric output limit permits an automatic corrected attempt.
    # An input/output sum needs provider counts; never derive it from bytes.
    output = re.search(r'(?:max_tokens|max_completion_tokens|output tokens)[^\d]{0,100}(?:at most|maximum(?: is| of)?|<=|less than or equal to)\s*(\d+)', lowered)
    if output is None:
        output = re.search(r'(?:maximum|at most)\s+(\d+)\s+(?:output|completion) tokens', lowered)
    supported = int(output.group(1)) if output else None
    input_count = re.search(r'(\d+) tokens from the input messages', lowered)
    window = re.search(r'maximum context length (?:of|is) (\d+) tokens', lowered)
    # OpenRouter can count message text and tool schemas separately. Use both
    # explicitly reported components, never our byte estimate or text alone.
    split_input = re.search(r'\((\d+) of text input,\s*(\d+) of tool input,\s*\d+ in the output\)', lowered)
    measured_input = (int(input_count.group(1)) if input_count else
                      sum(map(int, split_input.groups())) if split_input else None)
    allocated = re.search(r'(?:requested\s+(\d+)\s+output tokens|(\d+)\s+tokens for the completion|(\d+)\s+in the output)', lowered)
    lower_bound = re.search(r'prompt contains at least (\d+) input tokens', lowered)
    allocation = None
    if window and allocated:
        allocation = {'total_context': int(window.group(1)),
            'output_tokens': int(next(value for value in allocated.groups() if value is not None)),
            'input_tokens': measured_input if measured_input is not None else int(lower_bound.group(1)) if lower_bound else None,
            'input_count_kind': 'exact' if measured_input is not None else 'lower_bound' if lower_bound else 'unknown'}
        try:
            provider = json.loads(detail).get('error', {}).get('metadata', {}).get('provider_name')
        except (ValueError, AttributeError):
            provider = None
        if isinstance(provider, str) and provider:
            allocation['provider'] = provider
    if measured_input is not None and window:
        remaining = int(window.group(1)) - measured_input
        return ProviderCapacityRejected(detail, kind='combined' if remaining > 0 else 'input',
            response_path=response_path, supported_output_maximum=remaining if remaining > 0 else None,
            allocation=allocation)
    # Akash reports a combined-window failure as "requested ... output tokens"
    # and "prompt contains at least ... input tokens". That lower bound does
    # not establish either input-alone exhaustion or an exact output remainder.
    # In particular, mentioning output tokens does not make it output-only.
    prompt_and_output = (window is not None and 'prompt contains' in lowered
                         and 'input tokens' in lowered and 'output tokens' in lowered)
    combined = prompt_and_output or any(term in lowered for term in ('input and output', 'input + output', 'prompt and completion', 'including the completion', 'requested tokens'))
    kind = 'combined' if combined else ('output' if any(term in lowered for term in ('max_tokens', 'max_completion_tokens', 'output tokens')) else ('input' if 'input tokens' in lowered and ('exceed' in lowered or 'too many' in lowered) else 'ambiguous'))
    return ProviderCapacityRejected(detail, kind=kind, response_path=response_path,
                                    supported_output_maximum=supported if kind == 'output' else None,
                                    allocation=allocation)


class InfrastructureError(RuntimeError):
    pass


class AccountingError(InfrastructureError):
    """Shared accounting cannot be trusted; stop affected execution explicitly."""
    pass


class UncertainInference(InfrastructureError):
    """The request may have generated a response; never repeat it automatically."""
    pass


class ProviderRejected(InfrastructureError):
    """Provider rejection; only proved pre-generation conditions may retry."""
    pass


class ResponseRejected(InfrastructureError):
    """Complete transport, but its response cannot authorize any action."""
    pass


PRICES = {"standard": (.08, .004, .28), "expert": (2., .10, 10.)}
LIMITS = {"standard": (262144, 262144), "expert": (1000000, 393216)}
DEFAULT_OUTPUT_CEILING = None
USER_AGENT = "cs2680-expressive-agent/1.0"


@dataclass(frozen=True)
class ProviderPolicy:
    """Verified presentation behavior; limits and prices remain profile data."""
    name: str
    replay_text_reasoning: bool
    omit_old_reasoning: bool
    snapshot_placement: str = "append_existing_last"
    native_requires_tool_tail: bool = False

    def append_context(self, message, text):
        """Attach labelled context without inventing a new conversation turn."""
        if self.snapshot_placement != "append_existing_last" or message.get("role") not in ("user", "assistant", "tool"):
            raise ValueError("Unsupported provider context placement")
        result = dict(message)
        result["content"] = (message.get("content") or "") + text
        return result


_POLICIES = {
    "qwen": ProviderPolicy("qwen", True, True, native_requires_tool_tail=True),
    "archive_only": ProviderPolicy("archive_only", False, False),
}
# Current verified routes use this exact complete-response state matrix.
# Stream fragments are validated separately; they are not executable responses.
_FINISH_CALLS = {"stop": False, "tool_calls": True}


def provider_policy(profile):
    """Resolve a serializable policy key; legacy reasoning keys stay readable."""
    if isinstance(profile, ProviderPolicy):
        if profile not in _POLICIES.values():
            raise ValueError("Unverified provider policy")
        return profile
    name = profile if isinstance(profile, str) else (profile.get("policy", profile.get("reasoning"))
                                                   if isinstance(profile, dict) else None)
    if not isinstance(name, str) or name not in _POLICIES:
        raise ValueError(f"Unknown provider policy: {name!r}")
    return _POLICIES[name]


def normalize_message(message, policy, same_model=True):
    """Present visible text and only the verified compatible continuation."""
    policy = provider_policy(policy)
    result = {key: copy.deepcopy(value) for key, value in message.items()
              if key in ("role", "content", "tool_calls", "tool_call_id", "name")}
    if result["role"] == "assistant" and policy.replay_text_reasoning and same_model:
        text = message.get("reasoning_content") or message.get("reasoning")
        if not text:
            text = "".join(detail.get("text") or "" for detail in (message.get("reasoning_details") or [])
                           if detail.get("type") == "reasoning.text")
        if text:
            result["reasoning_content"] = text
    return result


def present_messages(messages, policy):
    """Project native continuation under the verified destination contract.

    Source ownership is checked by Context before this projection. Direct gateway
    requests already belong to their selected model. The qwen policy's historical
    omit_old_reasoning flag means the exact user boundary, never age or relevance.
    When an assistant ends the supplied history, these routes ignore historical
    native fields as well as that assistant's own field. Visible content is still
    consumed. Keep originals in Context; this presentation removes ignored fields
    before admission and transport. A later completed tool exchange restores
    historical eligibility. This is a tail contract, not a permanent cutoff.
    """
    policy = provider_policy(policy)
    result = [normalize_message(message, policy) for message in messages]
    if policy.omit_old_reasoning:
        latest_user = max((i for i, message in enumerate(result) if message['role'] == 'user'), default=-1)
        for message in result[:latest_user + 1]:
            message.pop('reasoning_content', None)
    if policy.native_requires_tool_tail and result and result[-1]['role'] == 'assistant':
        for message in result:
            message.pop('reasoning_content', None)
    return result


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


INPUT_CONTRACT_VERSION = 'receipt-prefix-v1'
INPUT_FRAMING_GUARD = 1024


def canonical_input(request, profile=None):
    messages = present_messages(request['messages'], profile) if profile is not None else request['messages']
    return encoded({'messages': messages, **({'tools': request['tools']} if 'tools' in request else {})})


def input_fingerprint(request, profile):
    """Identify the verified formatter and conditional consumed-field contract."""
    messages = present_messages(request['messages'], profile)
    boundary = max((i for i, message in enumerate(messages) if message['role'] == 'user'), default=-1)
    contract = profile.get('input_contract') if profile['backend'] == 'course' else INPUT_CONTRACT_VERSION
    if not isinstance(contract, str) or not contract:
        return None
    identity = {'version': INPUT_CONTRACT_VERSION, 'contract': contract, 'model': request.get('model'),
        'route': profile.get('route'), 'backend': profile['backend'], 'policy': vars(provider_policy(profile)),
        'profile_limits': {key: profile.get(key) for key in ('context', 'max_output', 'prices')},
        'tool_choice': request.get('tool_choice', 'auto'),
        'tools': request.get('tools'), 'user_boundary': messages[:boundary + 1],
        'tail': messages[-1]['role'] if messages else None,
        'settings': {key: request[key] for key in ('response_format', 'reasoning', 'reasoning_effort',
            'temperature', 'top_p', 'presence_penalty') if key in request}}
    return hashlib.sha256(encoded(identity)).hexdigest()


def input_receipt_key(request, profile, endpoint=None):
    """Bound retained anchors by route/endpoint/menu, not by conversation age."""
    return (request.get('model'), profile.get('route'), profile['backend'],
            hashlib.sha256(encoded(profile)).hexdigest(), endpoint,
            hashlib.sha256(encoded(request.get('tools'))).hexdigest())


@dataclass(frozen=True)
class InputReceipt:
    """Trusted settled input usage; canonical bytes stay local, never in RPC state."""
    canonical: bytes
    prompt_tokens: int
    fingerprint: str
    source: dict

    def public(self):
        return {**self.source, 'canonical_sha256': hashlib.sha256(self.canonical).hexdigest(),
                'prompt_tokens': self.prompt_tokens, 'fingerprint': self.fingerprint,
                'version': INPUT_CONTRACT_VERSION}

    @classmethod
    def from_public(cls, request, profile, value, usage):
        if not isinstance(value, dict) or value.get('version') != INPUT_CONTRACT_VERSION:
            return None
        raw, fingerprint = canonical_input(request, profile), input_fingerprint(request, profile)
        tokens = usage.get('prompt_tokens') if isinstance(usage, dict) else None
        if (not fingerprint or value.get('fingerprint') != fingerprint
                or value.get('canonical_sha256') != hashlib.sha256(raw).hexdigest()
                or type(tokens) is not int or tokens < 0 or value.get('prompt_tokens') != tokens):
            return None
        source = {key: value.get(key) for key in ('contract', 'endpoint', 'provider', 'call', 'request_path', 'response_path')}
        if source['contract'] != profile.get('input_contract'):
            return None
        return cls(raw, tokens, fingerprint, source)


def input_measure(request, previous=None, usage=None, *, profile=None, receipt=None):
    """Informational context estimates and conservative financial exposure.

    The course exposes no verified token counter. Serialized UTF-8 plus framing
    is deliberately a conservative estimate, not a claim of exact tokenization.
    Qualified settled total usage can bound a retained prefix. Every new suffix
    byte and explicit framing guard is charged at uncached prices; no cache hit
    is assumed. Missing/incompatible receipts retain the full UTF-8 estimate.
    """
    raw = canonical_input(request, profile)
    bound = full = len(raw) + INPUT_FRAMING_GUARD
    estimate = math.ceil(len(raw) / 3) + 512
    measured = None
    estimate_method = 'serialized consumed fields / 3 + framing estimate; exact provider tokenizer unavailable'
    if (isinstance(receipt, InputReceipt) and profile is not None
            and receipt.fingerprint == input_fingerprint(request, profile)):
        common = 0
        for left, right in zip(receipt.canonical, raw):
            if left != right: break
            common += 1
        suffix = len(raw) - common
        guard = INPUT_FRAMING_GUARD + 128 * len(request['messages'])
        estimate = math.ceil(receipt.prompt_tokens * common / max(1, len(receipt.canonical)) + suffix / 3) + 512
        estimate_method = 'settled provider receipt scaled to retained prefix + newly presented suffix / 3 + framing estimate'
        candidate = receipt.prompt_tokens + suffix + guard
        if candidate < bound:
            bound = candidate
            measured = {'source': receipt.public(), 'common_prefix_bytes': common,
                        'new_suffix_bytes': suffix, 'framing_guard': guard}
    if measured is None and previous and usage and previous.get('model') == request.get('model'):
        prior = canonical_input(previous, profile)
        tokens = usage.get('prompt_tokens')
        if type(tokens) is int and tokens >= 0:
            common = 0
            for left, right in zip(prior, raw):
                if left != right: break
                common += 1
            # This calibration is informational, not an admission bound. New
            # results must count, but a UTF-8 byte is not an observed token.
            estimate = math.ceil(tokens * common / max(1, len(prior)) + (len(raw) - common) / 3) + 512
            estimate_method = 'previous provider usage scaled to retained consumed prefix + newly presented suffix / 3 + framing estimate'
    return {'estimate': min(bound, estimate), 'upper_bound': bound, 'serialized_bytes': len(raw),
            'full_utf8_bound': full,
            'estimate_method': estimate_method,
            'method': ('receipt-backed total prompt + changed suffix bytes + framing; uncached-price estimate'
                       if measured else 'conservative UTF-8 estimate; exact provider tokenizer unavailable'),
            **({'receipt': measured} if measured else {}), 'exact': False}


def input_bound(request, profile=None, receipt=None):
    return input_measure(request, profile=profile, receipt=receipt)['upper_bound']


def response_capacity(request, profile, receipt=None):
    """Measure exposure and quote qualified routes; receipts never restrict routing."""
    presented = {**request, 'messages': present_messages(request['messages'], profile)}
    meter = input_measure(presented, profile=profile, receipt=receipt)
    return response_capacity_for_bound(meter['upper_bound'], profile,
                                      request.get('max_tokens', DEFAULT_OUTPUT_CEILING))


def reservation_input_bound(bound, profile):
    """Finite monetary exposure; never used to determine context fit."""
    return max(bound, profile['context'])


def response_capacity_for_bound(bound, profile, max_output_tokens=DEFAULT_OUTPUT_CEILING):
    """Quote provider output capacity; local money and input estimates never gate solving."""
    if type(bound) is not int or bound < 0:
        raise ValueError("Input bound must be a nonnegative integer")
    if max_output_tokens is not None and (type(max_output_tokens) is not int or max_output_tokens < 1):
        raise ValueError('Output ceiling must be a positive integer or None for the provider maximum')
    provider_policy(profile)
    physical = profile["max_output"]
    maximum = physical if max_output_tokens is None else min(physical, max_output_tokens)
    if maximum < 1:
        raise ValueError("No verified provider output capacity is available")
    # This is a financial reservation estimate, not a verified input token count.
    # The provider decides input fit; never subtract UTF-8 bytes from its window.
    return bound, maximum, []


def course_cost(usage, prices, *, exact=False):
    prompt = usage["prompt_tokens"]
    completion = usage["completion_tokens"]
    details = usage.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        raise AccountingError("Invalid provider prompt-token detail container")
    cached = (details or {}).get("cached_tokens", usage.get("cached_tokens", 0)) or 0
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (prompt, completion, cached)) or cached > prompt:
        raise AccountingError("Invalid provider token accounting")
    rates = [Decimal(str(rate)) for rate in prices]
    value = ((prompt - cached) * rates[0] + cached * rates[1] + completion * rates[2]) / Decimal(1000000)
    return value if exact else float(value)


def discover_profiles(base, key, artifact_dir=None):
    """Course tiers from the course /models listing; routes must be the course aliases."""
    req = urllib.request.Request(base.rstrip("/") + "/models",
                                 headers={"Authorization": "Bearer " + key, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read()
    listing = json.loads(raw)
    if artifact_dir:
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
        (Path(artifact_dir) / "models.json").write_bytes(raw)
    data = {x["id"]: x for x in listing["data"]}
    profiles, unavailable = {}, {}
    endpoint = urllib.parse.urlsplit(base)
    # Route identity must not carry URL credentials or query parameters.
    address = endpoint.hostname or ''
    if ':' in address:
        address = '[' + address + ']'
    if endpoint.port is not None:
        address += ':' + str(endpoint.port)
    endpoint = urllib.parse.urlunsplit((endpoint.scheme, address, endpoint.path.rstrip('/'), '', ''))
    for alias in ("standard", "expert"):
        route = os.environ.get("CS2680_MODEL_" + alias.upper(), alias)
        if route != alias:
            raise InfrastructureError(f"Unverified course route mapping for {alias}: {route!r}; expected {alias!r}. No inference started")
        if route not in data:
            unavailable[alias] = f"Course model {route} is absent from /models"
            continue
        item = data[route]
        context = item.get("context_length", LIMITS[alias][0])
        output = item.get("max_output_length") or LIMITS[alias][1]
        p = item["pricing"]
        prices = [float(p["prompt"]), float(p["input_cache_reads"]), float(p["completion"])]
        policy = "qwen" if alias == "standard" else "archive_only"
        profiles[alias] = {"route": route, "context": context, "max_output": output, "endpoint": endpoint,
                           "prices": prices, "policy": policy, "reasoning": policy,
                           "backend": "course", "input_contract": 'course-20261015-text-usage-v1'}
        # Actual course metadata and retained actual-course required-mode
        # read/continuation probes establish these. A development reroute
        # serves the same saved course metadata; its substitutes are not
        # course capability evidence.
        if type(item.get('context_length')) is int and item['context_length'] > 0:
            profiles[alias]['usable_context'] = {'tokens': item['context_length'],
                'source': f'course /v1/models {route}.context_length; output shares provider capacity'}
        profiles[alias]['maintenance_tool_choice'] = 'required'
        profiles[alias]['maintenance_tool_choice_source'] = 'actual course required-tool continuation probes 2026-10-15; reasoning defaults unchanged'
    if artifact_dir and unavailable:
        (Path(artifact_dir) / "unavailable-models.json").write_text(json.dumps(unavailable, indent=2))
    if "standard" not in profiles:
        raise InfrastructureError("The standard model is not present in current provider metadata")
    return profiles

def validate_response(answer):
    """Validate the complete action envelope before examining or using its calls."""
    if not isinstance(answer, dict):
        raise ValueError("Response envelope must be an object")
    if answer.get("rejection"):
        raise ValueError(str(answer["rejection"]))
    finish = answer.get("finish_reason")
    if not isinstance(finish, str) or finish not in _FINISH_CALLS:
        raise ValueError(f"Unsupported or incomplete finish_reason={finish!r}; only stop and tool_calls are accepted")
    message = answer.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("Provider message must have assistant role")
    if message.get("function_call") is not None:
        raise ValueError("Legacy function_call responses are unsupported; use tool_calls")
    for key in ("content", "reasoning", "reasoning_content"):
        if message.get(key) is not None and not isinstance(message[key], str):
            raise ValueError(f"Assistant {key} must be text or null")
    details = message.get("reasoning_details")
    if details is not None and (not isinstance(details, list) or any(
            not isinstance(d, dict) or (d.get("text") is not None and not isinstance(d["text"], str)) for d in details)):
        raise ValueError("Assistant reasoning_details must contain objects with text fragments")
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
        raise ValueError("Assistant tool_calls must be a list of tool-call objects, never null")
    if bool(calls) != _FINISH_CALLS[finish]:
        raise ValueError(f"finish_reason={finish!r} does not match the presence of tool calls")
    return message


@dataclass(frozen=True)
class ResponseEnvelope:
    frames: tuple
    stream: bool
    complete: bool
    usage: dict
    provider: str | None
    error: str | None = None
    accounting_error: str | None = None


def response_envelope(raw):
    """Interpret identity, ordering, and receipts once, separately from actions."""
    text = raw.decode("utf-8", errors="replace")
    stream = any(line.startswith("data:") for line in text.splitlines())
    frames, usage, provider, generation, finish = [], {}, None, None, None
    done, error, accounting_error = False, None, None
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        error = accounting_error = "Malformed UTF-8 provider envelope"
    values = (line[5:].strip() for line in text.splitlines() if line.startswith("data:")) if stream else [text]
    for value in values:
        if value == "[DONE]":
            done = True
            continue
        if done:
            error = accounting_error = "Provider sent response data after stream completion"
        try:
            part = json.loads(value)
        except ValueError:
            # A truncated final frame does not erase an earlier independent
            # receipt. It cannot authorize an action or supply a new receipt.
            error = error or "Malformed provider JSON frame"
            continue
        if not isinstance(part, dict):
            error = accounting_error = "Provider response frame must be an object"
            continue
        frames.append(part)
        identifier = part.get("id")
        if identifier is not None:
            if not isinstance(identifier, str) or not identifier or generation not in (None, identifier):
                error = accounting_error = "Conflicting or malformed provider response IDs; no tools authorized"
            else:
                generation = identifier
        incoming = part.get("usage")
        if incoming is not None and not isinstance(incoming, dict):
            accounting_error = "Malformed provider usage receipt"
        elif incoming:
            # Nested billing objects are whole receipt fields: merge only
            # complementary top-level fields, never infer a nested repair.
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens",
                        "prompt_tokens_details", "completion_tokens_details", "cost", "cost_details"):
                if key in usage and key in incoming and usage[key] != incoming[key]:
                    accounting_error = accounting_error or "Conflicting provider usage receipts"
            usage = {**usage, **incoming}
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict) and "cached_tokens" in details and "cached_tokens" in usage:
                outer, nested = usage["cached_tokens"], details["cached_tokens"]
                if type(outer) is not type(nested) or outer != nested:
                    accounting_error = accounting_error or "Conflicting provider cache-token aliases"
        provider = part.get("provider") or provider
        if not stream:
            continue
        choices = part.get("choices", [])
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            delta, next_finish = choice.get("delta", {}), choice.get("finish_reason")
            if finish is not None and isinstance(delta, dict) and any(delta.get(key) for key in
                    ("content", "reasoning", "reasoning_content", "reasoning_details", "tool_calls")):
                error = accounting_error = "Provider sent generated content after its final finish reason"
            if next_finish is not None:
                if not isinstance(next_finish, str):
                    error = error or "Malformed provider finish reason"
                elif finish not in (None, next_finish):
                    error = accounting_error = "Conflicting provider finish reasons"
                else:
                    finish = next_finish
    error = error or accounting_error
    complete = bool(text.strip()) and (not stream or done or (not error and _terminal_stream_error(text, frames)))
    return ResponseEnvelope(tuple(frames), stream, complete,
                            {} if accounting_error else usage,
                            None if accounting_error else provider, error, accounting_error)


def course_accounting_refusal(status, envelope):
    """Recognize the course endpoint's explicit pre-forward accounting refusal.

    A generic outage, text mentioning accounting, or a response containing usage
    or generation cannot establish that this request incurred no inference.
    Earlier requests' uncertain charges are independent of this admission fact.
    """
    if (status != 503 or envelope.stream or not envelope.complete or envelope.error
            or len(envelope.frames) != 1):
        return None
    body = envelope.frames[0]
    error = body.get("error")
    if (set(body) != {"error", "inference_forwarded"} or body["inference_forwarded"] is not False
            or not isinstance(error, dict) or set(error) != {"message", "type"}
            or not isinstance(error["message"], str)
            or error["type"] not in ("course_accounting_uncertain", "course_accounting_persistence_failed")):
        return None
    return error["type"]


def _parse_response(envelope):
    """Assemble actions only after the shared envelope has been admitted."""
    if not envelope.stream:
        body = envelope.frames[0]
        if not isinstance(body, dict):
            raise ResponseRejected("Provider response body must be an object")
        if body.get("error") is not None:
            raise ResponseRejected("Provider returned an error body")
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ResponseRejected("Provider response needs exactly one choice object")
        choice = choices[0]
        if choice.get("error") is not None:
            raise ResponseRejected("Provider choice contains an error")
        return {"message": choice["message"], "finish_reason": choice["finish_reason"],
                "usage": body.get("usage", {}), "provider": body.get("provider")}
    message = {"role": "assistant", "content": ""}
    calls, finish = {}, None
    for part in envelope.frames:
        if part.get("error") is not None:
            raise ResponseRejected("Provider stream returned an error; no tools authorized")
        choices = part.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1 or any(not isinstance(c, dict) for c in choices):
            raise ResponseRejected("Provider stream choices must contain at most one choice object")
        for choice in choices:
            if type(choice.get("index", 0)) is not int or choice.get("index", 0) != 0:
                raise ResponseRejected("Unexpected provider choice index")
            if choice.get("error") is not None:
                raise ResponseRejected("Provider choice contains an error")
            next_finish = choice.get("finish_reason")
            if next_finish is not None:
                finish = next_finish
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise ResponseRejected("Provider stream delta must be an object")
            if delta.get("role") not in (None, "assistant"):
                raise ResponseRejected("Provider stream role must be assistant")
            for key in ("content", "reasoning", "reasoning_content"):
                value = delta.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ResponseRejected(f"Provider {key} fragment must be text or null")
                    message[key] = message.get(key, "") + value
            if "reasoning_details" in delta and delta["reasoning_details"] is not None:
                incoming = delta["reasoning_details"]
                if not isinstance(incoming, list) or any(not isinstance(d, dict) for d in incoming):
                    raise ResponseRejected("Provider reasoning_details must contain objects")
                # Text blocks can be fragmented by index; encrypted blocks stay exact.
                details = message.setdefault("reasoning_details", [])
                for detail in incoming:
                    if detail.get("text") is not None and not isinstance(detail["text"], str):
                        raise ResponseRejected("Provider reasoning text must be a string")
                    match = next((d for d in details if d.get("index") == detail.get("index") and d.get("type") == detail.get("type")), None)
                    if match is not None and detail.get("text") is not None:
                        match["text"] = match.get("text", "") + detail["text"]
                    else:
                        details.append(copy.deepcopy(detail))
            incoming = delta.get("tool_calls", [])
            if not isinstance(incoming, list) or any(not isinstance(c, dict) for c in incoming):
                raise ResponseRejected("Provider tool_calls must be a list of objects, never null")
            for call in incoming:
                if type(call.get("index")) is not int or call["index"] < 0:
                    raise ResponseRejected("Tool-call fragment needs a nonnegative integer index")
                if call.get("type") not in (None, "function"):
                    raise ResponseRejected("Only function tool-call fragments are supported")
                dst = calls.setdefault(call["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if call.get("id") is not None:
                    if not isinstance(call["id"], str):
                        raise ResponseRejected("Tool-call id fragment must be text or null")
                    dst["id"] += call["id"]
                function = call.get("function")
                if function is None:
                    function = {}
                if not isinstance(function, dict):
                    raise ResponseRejected("Malformed function delta; no tools executed")
                for key in ("name", "arguments"):
                    fragment = function.get(key)
                    # Course SSE explicitly sends null for unchanged fields.
                    # Absence is not text; never coerce or repair arguments.
                    if fragment is not None:
                        if not isinstance(fragment, str):
                            raise ResponseRejected("Non-string tool-call fragment; no tools executed")
                        dst["function"][key] += fragment
    if not finish:
        raise ResponseRejected("Completed provider stream omitted finish_reason")
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"message": message, "finish_reason": finish, "usage": envelope.usage, "provider": envelope.provider}


def _terminal_stream_error(text, frames):
    """Recognize only a complete rejected SSE envelope without a success marker.

    OpenRouter can split its terminal error, error finish, and usage over three
    frames and omit DONE. Validate the entire sequence before classifying it;
    this path never authorizes generated content or tools.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n\n"):
        return False
    rejected, finished, events = False, False, 0
    try:
        for event in text.split("\n\n"):
            lines = [line for line in event.splitlines() if line and not line.startswith(":")]
            if not lines:
                continue
            if len(lines) != 1 or not lines[0].startswith("data:"):
                return False
            events += 1
        if events != len(frames):
            return False
        for part in frames:
            if part.get("usage") is not None and not isinstance(part["usage"], dict):
                return False
            error = part.get("error")
            if error is not None:
                if (not isinstance(error, dict) or type(error.get("code")) not in (int, str)
                        or not error["code"] or not isinstance(error.get("message"), str)
                        or not error["message"] or (error.get("metadata") is not None
                                                   and not isinstance(error["metadata"], dict))):
                    return False
                rejected = True
            choices = part.get("choices")
            if not isinstance(choices, list) or len(choices) > 1:
                return False
            for choice in choices:
                if not isinstance(choice, dict) or type(choice.get("index", 0)) is not int or choice.get("index", 0) != 0:
                    return False
                delta = choice.get("delta", {})
                if not isinstance(delta, dict) or delta.get("role") not in (None, "assistant"):
                    return False
                if rejected and any(value for key, value in delta.items() if key != "role"):
                    return False
                finish = choice.get("finish_reason")
                if finish is not None:
                    if not rejected or finish != "error":
                        return False
                    finished = True
    except (ValueError, TypeError):
        return False
    return rejected and finished


def parse_response(raw, *, envelope=None):
    # An unfinished transport remains uncertain. A complete invalid response is
    # a recoverable rejection, never a partial tool batch or an implicit stop.
    envelope = envelope or response_envelope(raw)
    if not envelope.complete:
        raise InfrastructureError("Incomplete stream; no tool call was executed")
    if envelope.error:
        raise ResponseRejected(envelope.error)
    try:
        return _parse_response(envelope)
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ResponseRejected("Malformed complete provider response; no tools executed") from error


def response_metadata(raw, *, envelope=None):
    """Recover independent usage receipts even when message assembly fails.

    Identity/termination contradictions invalidate attribution. Action errors
    alone do not erase a separately valid charge for that same response.
    """
    envelope = envelope or response_envelope(raw)
    return envelope.usage, envelope.provider


class TraceDelivery:
    """Required course events are retained before bounded asynchronous delivery.

    The spool is evidence storage, like requests and observations; its writes
    are required. A slow course report sink never holds the execution thread.
    An ambiguous delivery is retained for reconciliation, never blindly retried.
    """
    def __init__(self, runtime, log):
        self.runtime, self.log = runtime, log
        self.spool = runtime / "trace-spool"
        self.spool.mkdir(parents=True, exist_ok=True)
        self.condition = threading.Condition()
        self.errors = deque(maxlen=16)
        self.total = self.pending = self.delivered = self.uncertain = self.unretained = self.error_count = 0
        self.cursor = 1
        self.closed = False
        self.worker = None
        for path in sorted(self.spool.glob('*.event.json')):
            self.total = max(self.total, int(path.name.split('.')[0]))
            state = self._read_state(path)
            if state == 'delivered':
                self.delivered += 1
            elif state in ('attempting', 'uncertain'):
                self.uncertain += 1  # A lost acknowledgement does not establish non-delivery.
            else:
                self.pending += 1
        if self.pending:
            self._start()

    def _path(self, number):
        return self.spool / f'{number:08d}.event.json'

    @staticmethod
    def _read_state(path):
        try:
            value = json.loads(path.with_suffix('.state.json').read_text())
            state = value.get('state') if isinstance(value, dict) else None
            return state if state in ('delivered', 'attempting', 'uncertain') else 'uncertain'
        except FileNotFoundError:
            return 'pending'
        except (OSError, ValueError, KeyError):
            return 'uncertain'

    def _start(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._deliver, daemon=True, name="course-trace")
            try:
                self.worker.start()
            except RuntimeError as error:
                self.worker = None
                self._error('trace', 'worker', error)

    @staticmethod
    def _write_state(path, state):
        destination = path.with_suffix('.state.json')
        temporary = destination.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps({'state': state}))
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def record(self, method, fields):
        try:
            raw = encoded({"event": method, **fields})
        except (TypeError, ValueError) as error:
            self.unretained += 1
            self._error(method, "encoding", error)
            return
        with self.condition:
            if self.closed:
                self.unretained += 1
                self._error(method, 'spool', RuntimeError('Trace admission is closed'))
                return
            number = self.total + 1
        # Retention is essential evidence I/O, not a best-effort queue slot.
        # record has one producer at each execution location; sinks run elsewhere.
        path = self._path(number)
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw)
            temporary.replace(path)
        except OSError as error:
            with self.condition:
                self.unretained += 1
                self._error(method, 'spool', error)
            return
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                self._error(method, 'spool_cleanup', error)
        with self.condition:
            self.total = number
            self.pending += 1
            self._start()
            self.condition.notify_all()

    def _error(self, method, sink, error):
        with self.condition:
            self.error_count += 1
            self.errors.append({"event": method, "sink": sink,
                                "error": (type(error).__name__ + ": " + str(error))[:1000]})

    def _deliver(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.cursor <= self.total or self.closed)
                if self.closed:
                    return
                path = self._path(self.cursor)
                self.cursor += 1
            if self._read_state(path) != 'pending':
                continue
            method, delivered = 'trace', False
            try:
                raw = path.read_bytes()
                event = json.loads(raw)
                method = event.pop('event')
                # Persist the start before a sink can accept the event. On restart
                # an attempting record is uncertain, not safe to resend.
                self._write_state(path, 'attempting')
            except Exception as error:
                # Known before delivery: leave the retained event pending, not
                # uncertain. A future owner can resume it once storage is repaired.
                self._error(method, 'delivery_not_started', error)
                with self.condition:
                    self.cursor -= 1
                    self.worker = None
                    self.condition.notify_all()
                return
            try:
                self.log(method, event)
                delivered = True
            except Exception as error:
                self._error(method, 'course', error)
            try:
                self._write_state(path, 'delivered' if delivered else 'uncertain')
            except Exception as error:
                self._error(method, 'acknowledgement', error)
            with self.condition:
                self.pending -= 1
                self.delivered += int(delivered)
                self.uncertain += int(not delivered)
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {"pending": self.pending, "attempted": self.delivered + self.uncertain,
                    "delivered": self.delivered, "uncertain": self.uncertain,
                    "unretained": self.unretained, "dropped": 0, "retained": self.total,
                    "spool_path": str(self.spool), "error_count": self.error_count,
                    "errors": list(self.errors), "complete": not (self.pending or self.uncertain or self.unretained),
                    "recovery": 'Never-attempted records can resume delivery. Attempting/uncertain records require reconciliation; no model or tool action is repeated.'}

    def close(self, wait_seconds=0.2):
        deadline = time.monotonic() + wait_seconds
        with self.condition:
            while self.pending and time.monotonic() < deadline:
                self.condition.wait(max(0, deadline - time.monotonic()))
            # Keep every deferred event on disk. A live sink may finish later;
            # the returned snapshot is explicit about incomplete course delivery.
            self.closed = True
            self.condition.notify_all()
        return {**self.snapshot(), "drain_seconds": wait_seconds}


class Gateway:
    def __init__(self, base, key, profiles, artifact_dir, logger=None, *, run_control=None,
                 task_id=None):
        self.base, self.key, self.profiles = base.rstrip("/"), key, profiles
        self.root = Path(artifact_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.task_id = task_id
        self.run_control = run_control
        self.rows = []
        self.input_receipts = {}
        self.capacity_observations = {}
        self.disabled_input_contracts = set()
        self.lock = threading.RLock()
        self.trace = TraceDelivery(self.root, logger) if logger else None

    def state(self):
        charged = sum(r["course_price"] for r in self.rows if "course_price" in r)
        reserved = sum(r["reservation"] for r in self.rows if "course_price" not in r)
        # Keep the legacy exposure field for admission/recovery callers. A quote
        # held for an inflight or unknown response is not a settled charge.
        spent = sum(r.get("course_price", r["reservation"]) for r in self.rows)
        result = {"spent": spent, "charged": charged, "reserved": reserved,
                "remaining": None, "limit": None,
                "calls": len(self.rows), "actual_cost": sum(r.get("actual_cost") or 0 for r in self.rows),
                "actual_cost_unknown_calls": sum(r.get("actual_cost") is None for r in self.rows),
                "uncertain_calls": sum("course_price" not in r for r in self.rows),
                "trace_delivery": self.trace.snapshot() if self.trace else None,
                "capacity_observations": copy.deepcopy({alias: value for alias, value in self.capacity_observations.items()
                    if alias in self.profiles and value['scope'] == capacity_scope(self.profiles[alias])})}
        if self.rows and type(self.rows[-1].get('call')) is int:
            last = self.rows[-1]
            folder = self.root / f"call-{last['call']:04d}"
            result['last_attempt'] = {key: last[key] for key in
                ('call', 'model', 'reservation', 'course_price', 'error', 'accounting_reason', 'response_bytes',
                 'requested_output_ceiling', 'admitted_output_maximum', 'reservation_output_maximum', 'input_receipt_invalidated', 'provider_capacity', 'retry_after_seconds') if key in last}
            result['last_attempt'].update(gateway_completed='seconds' in last,
                reservation_retained='course_price' not in last,
                request_path=str(folder / 'request.json'), response_path=str(folder / 'response.raw'))
        if self.run_control is not None:
            result["run_budget"] = self.run_control.snapshot()
            if self.task_id is not None:
                result["task_allocation"] = self.run_control.task_state(self.task_id)
        return result

    def close_reporting(self, wait_seconds=.2):
        return self.trace.close(wait_seconds) if self.trace else None

    def save(self):
        try:
            (self.root / "metrics.json").write_text(json.dumps({**self.state(), "attempts": self.rows}, indent=2, allow_nan=False))
        except (OSError, ValueError, TypeError) as error:
            raise AccountingError("Unable to retain shared accounting receipt") from error

    def complete(self, request):
        with self.lock:
            try:
                answer = self._complete(request)
                # _complete's finally settles the shared reservation before the
                # caller receives the current budget, including this charge.
                return {**answer, "budget": self.state()}
            except RunHalted as error:
                raise InfrastructureError(str(error)) from error
            except RunControlError as error:
                if self.run_control is not None:
                    self.run_control.stop(str(error))
                raise AccountingError(str(error)) from error
            except AccountingError as error:
                if self.run_control is not None:
                    self.run_control.stop(str(error))
                raise

    def _complete(self, request):
        alias = request["model"]
        if alias not in self.profiles:
            raise ValueError("Model unavailable: " + alias)
        profile = self.profiles[alias]
        request = {**request, 'messages': present_messages(request['messages'], profile)}
        contract_key = (alias, hashlib.sha256(encoded(profile)).hexdigest())
        fingerprint = None if contract_key in self.disabled_input_contracts else input_fingerprint(request, profile)
        receipt = next((value for value in reversed(self.input_receipts.values())
                        if value.fingerprint == fingerprint), None)
        requested_output_ceiling = request.get('max_tokens', DEFAULT_OUTPUT_CEILING)
        use_provider_default = False
        corrections = rate_limits = 0
        while True:
            self._require_open()
            capacity = response_capacity(request, profile, receipt)
            bound, maximum, _ = capacity
            run_token = None
            if self.run_control is not None:
                def quote(_):
                    cost = (reservation_input_bound(bound, profile) * profile["prices"][0] + maximum * profile["prices"][2]) / 1e6
                    return cost, capacity
                try:
                    run_token, (bound, maximum, _) = self.run_control.reserve(
                        f"{self.root.resolve()}:{len(self.rows) + 1}", quote, task_id=self.task_id)
                except RunControlError as error:
                    raise AccountingError(str(error)) from error
                except RunHalted as error:
                    raise InfrastructureError(str(error)) from error
            row, request_started = None, False
            try:
                prices = profile["prices"]
                wire = copy.deepcopy(request)
                wire.update(model=profile["route"], max_tokens=maximum, stream=True, stream_options={"include_usage": True})
                provider_default = (use_provider_default or (request.get('max_tokens') is None
                                    and profile['backend'] == 'course'))
                if provider_default:
                    # Omitted allocation delegates to the provider. Injecting the
                    # advertised output maximum caused a combined-capacity
                    # rejection on every growing request: that maximum also
                    # needs room for the input. Do not replace it with a stale
                    # prior count or a byte-derived cap. The actual default is
                    # unknown; reserve the finite advertised maximum.
                    wire.pop('max_tokens', None)
                admitted_maximum = None if provider_default else maximum
                reservation = (reservation_input_bound(bound, profile) * prices[0] + maximum * prices[2]) / 1e6
                number = len(self.rows) + 1
                folder = self.root / f"call-{number:04d}"
                folder.mkdir()
                raw = encoded(wire)
                (folder / "request.json").write_bytes(raw)
                row = {"call": number, "model": alias, "input_measure": input_measure(request, profile=profile, receipt=receipt), "reservation": reservation,
                       "requested_output_ceiling": requested_output_ceiling, "admitted_output_maximum": admitted_maximum,
                       "reservation_output_maximum": maximum,
                       "monetary_input_reservation_tokens": reservation_input_bound(bound, profile),
                       "status": "inflight", "started": time.time()}
                self.rows.append(row)
                self.save()
                response, status = b"", None
                response_path = folder / "response.raw"
                response_path.touch()
                retry_delay = None
                try:
                    headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                    req = urllib.request.Request(self.base + "/chat/completions", data=raw,
                                                 headers=headers)
                    self._require_open()
                    try:
                        request_started = True
                        reply = urllib.request.urlopen(req, timeout=900)
                    except urllib.error.HTTPError as error:
                        reply = error
                    except Exception as error:
                        raise UncertainInference("Upstream delivery uncertain before response headers; request was not repeated") from error
                    with reply:
                        status = reply.status
                        row["status"] = status
                        retry_after = reply.headers.get("Retry-After")
                        if retry_after:
                            from email.utils import parsedate_to_datetime
                            try:
                                try:
                                    seconds = float(retry_after)
                                except ValueError:
                                    seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                                if math.isfinite(seconds):
                                    row["retry_after_seconds"] = max(0., seconds)
                            except (ValueError, TypeError, OverflowError):
                                pass
                        (folder / "response-metadata.json").write_bytes(encoded({"status": status,
                            "headers": {k: reply.headers.get(k) for k in ("Content-Type", "Content-Length", "Retry-After")}}))
                        try:
                            with response_path.open("wb") as archive:
                                while True:
                                    try:
                                        chunk = reply.read1(65536)
                                    except http.client.IncompleteRead as error:
                                        if error.partial:
                                            archive.write(error.partial)
                                            archive.flush()
                                        raise
                                    if not chunk:
                                        break
                                    archive.write(chunk)
                                    archive.flush()
                                if reply.length not in (None, 0):
                                    raise http.client.IncompleteRead(b"", reply.length)
                        except Exception as error:
                            raise UncertainInference("Upstream response delivery interrupted; partial bytes retained; request was not repeated") from error
                    response = response_path.read_bytes()
                    envelope = response_envelope(response)
                    refusal = course_accounting_refusal(status, envelope) if profile['backend'] == 'course' else None
                    if refusal:
                        row.update(course_price=0., actual_cost=0., inference_forwarded=False,
                                   termination=refusal, accounting_status="admission_refused",
                                   accounting_reason="Course endpoint explicitly refused this request before inference")
                        raise AccountingError(
                            f"Course inference admission stopped: {refusal}; this request was not forwarded. "
                            f"Earlier uncertain charges remain unresolved. Exact response: {response_path}")
                    self._account_response(row, response, prices, envelope=envelope)
                    if envelope.accounting_error:
                        raise UncertainInference(envelope.accounting_error +
                            "; full reservation retained; inference was not repeated")
                    if status != 200:
                        if status in (400, 401, 403, 404, 413, 422, 429):
                            # HTTP rejection alone cannot override an attributed
                            # receipt or authorize replay after generation began.
                            if envelope.usage or any(frame.get("choices") for frame in envelope.frames):
                                raise ProviderRejected(f"Provider HTTP {status} returned generation or usage evidence; "
                                    "observed charges or reservation retained; no tools executed or inference repeated")
                            row["course_price"] = 0.
                        detail = response.decode(errors="replace")
                        if status == 429 and any(x in detail.lower() for x in ("quota_usd", "remaining_usd", "budget exhausted", "insufficient credit")):
                            raise BudgetExceeded("Provider budget exhausted")
                        if (status in (400, 422) and wire.get('tool_choice') == 'required'
                                and 'tool_choice' in detail.lower() and 'required' in detail.lower()
                                and any(term in detail.lower() for term in ('unsupported', 'not supported', 'unavailable'))):
                            # This is definite capability rejection, not failed
                            # generation or tool execution. Keep its reason across
                            # the existing gateway/RPC exception boundary.
                            raise ProviderRejected(f"Provider rejected tool_choice='required' before generation (HTTP {status}); "
                                                   f"no tool actions executed. Exact response: {response_path}")
                        if status in (400, 413, 422):
                            capacity_error = provider_capacity_rejection(detail, response_path)
                            if capacity_error:
                                row['provider_capacity'] = {'kind': capacity_error.kind,
                                    'supported_output_maximum': capacity_error.supported_output_maximum,
                                    'response_path': str(response_path), 'generation_started': False}
                                allocation = capacity_error.allocation
                                if allocation is not None:
                                    row['provider_capacity']['allocation'] = allocation
                                    boundary = allocation['total_context'] - allocation['output_tokens']
                                    if 'max_tokens' not in wire and boundary > 0 and allocation['output_tokens'] > 0:
                                        # The receipt establishes this attempt's admission boundary,
                                        # not a permanent model limit or the next provider's default.
                                        observation = {**allocation, 'tokens': boundary, 'allocation': 'provider_default',
                                            'scope': capacity_scope(profile), 'call': number,
                                            'response_path': str(response_path),
                                            'response_sha256': hashlib.sha256(response).hexdigest(),
                                            'source': f'Observed provider-default allocation in rejected call {number}; '
                                                f'exact response: {response_path}. This is a scheduling observation, '
                                                'not a verified default for every future upstream.'}
                                        row['input_capacity_observation'] = observation
                                        self.capacity_observations[alias] = observation
                                correction = capacity_error.supported_output_maximum
                                if correction is not None and 0 < correction < maximum and corrections < 2:
                                    request = {**request, 'max_tokens': correction}
                                    row['output_allocation_correction'] = correction
                                    retry_delay = min(2 ** corrections, 4)
                                    corrections += 1
                                    raise ProviderRejected('Provider explicitly rejected output allocation; retrying only its stated supported maximum, preserving input')
                                if (capacity_error.kind == 'output' and correction is None
                                        and 'max_tokens' in wire and corrections < 2):
                                    # A definite pre-generation allocation rejection
                                    # can fall back to the provider default once.
                                    # Preserve input and never invent a smaller cap.
                                    use_provider_default = True
                                    row['output_allocation_correction'] = 'provider_default'
                                    retry_delay = min(2 ** corrections, 4)
                                    corrections += 1
                                    raise ProviderRejected('Provider rejected explicit output allocation; retrying with provider default and unchanged input')
                                raise capacity_error
                        if status == 429:
                            retry_delay = row.get('retry_after_seconds', min(2 ** min(rate_limits, 2), 4))
                            rate_limits += 1
                            row.update(disposition='rate_limited_before_generation',
                                       generation_started=False, retry_delay_seconds=retry_delay)
                            raise ProviderRejected("Provider rate-limited before generation; no tool actions executed; retrying after provider pacing")
                        if status >= 500:
                            raise UncertainInference(f"Provider HTTP {status}; generation outcome unknown; request was not repeated")
                        raise ProviderRejected(f"Provider HTTP {status}; inspect archived response")
                    try:
                        answer = parse_response(response, envelope=envelope)
                    except ResponseRejected as error:
                        usage, provider = response_metadata(response, envelope=envelope)
                        answer = {"message": None, "finish_reason": None, "usage": usage,
                                  "provider": provider, "rejection": str(error)}
                    except InfrastructureError as error:
                        raise UncertainInference("Provider response incomplete or malformed; exact bytes retained; no tools executed or inference repeated") from error
                    if "course_price" not in row:
                        detail = row.get("accounting_reason") or "Provider omitted complete usage"
                        raise UncertainInference(detail + "; full reservation retained; inference was not repeated")
                    try:
                        validate_response(answer)
                    except ValueError as error:
                        answer["rejection"] = str(error)
                        row["response_rejection"] = str(error)
                    retained, invalidated = None, False
                    provider = answer.get('provider')
                    observed_capacity = self.capacity_observations.get(alias)
                    if observed_capacity is not None:
                        upstream_changed = (provider and observed_capacity.get('provider')
                                            and provider != observed_capacity['provider'])
                        larger_input_accepted = ('max_tokens' not in wire
                            and answer['usage']['prompt_tokens'] > observed_capacity['tokens'])
                        if upstream_changed or larger_input_accepted:
                            row['input_capacity_observation_invalidated'] = (
                                'Reported upstream changed' if upstream_changed else
                                'Provider accepted uncapped input beyond the observed allocation boundary')
                            self.capacity_observations.pop(alias)
                    falsified = row['input_measure'].get('receipt') and answer['usage']['prompt_tokens'] > bound
                    if falsified:
                        cause = (f'Actual verified prompt total {answer["usage"]["prompt_tokens"]} exceeded '
                                 f'the receipt-backed conservative input estimate {bound}. The prior estimate is invalidated. ')
                        row['input_estimate_discrepancy'] = (cause +
                            'Exact response and usage retained. This measurement discrepancy does not reject a valid action; accounting is settled separately.')
                        self.input_receipts.clear()
                        row['input_receipt_invalidated'] = True
                        invalidated = True
                    if fingerprint and profile['backend'] == 'course':
                        source = {'contract': profile.get('input_contract', INPUT_CONTRACT_VERSION),
                            'endpoint': None, 'provider': provider,
                            'call': number, 'request_path': str(folder / 'request.json'),
                            'response_path': str(response_path)}
                        retained = InputReceipt(canonical_input(request, profile), answer['usage']['prompt_tokens'],
                                                fingerprint, source)
                        key = input_receipt_key(request, profile, source['endpoint'])
                        self.input_receipts.pop(key, None)
                        self.input_receipts[key] = retained
                        row['input_receipt'] = retained.public()
                    return {**answer, "raw_response_path": str(response_path), "budget": self.state(),
                            **({'input_receipt': retained.public()} if retained else {}),
                            **({'input_receipt_invalidated': True} if invalidated else {}),
                            "generation_allocation": {"requested_ceiling": requested_output_ceiling,
                                                      "admitted_maximum": admitted_maximum, "reservation_output_maximum": maximum}}
                except ProviderRejected as error:
                    row["error"] = type(error).__name__ + ": " + str(error)
                    if retry_delay is None:
                        raise
                except Exception as error:
                    row["error"] = type(error).__name__ + ": " + str(error)
                    raise
                finally:
                    # Even a dropped body can contain an independently valid usage receipt.
                    accounting_error = None
                    try:
                        if row.get("inference_forwarded") is not False:
                            self._account_response(row, response_path.read_bytes(), prices)
                    except AccountingError as error:
                        accounting_error = error
                        row["accounting_error"] = str(error)
                    row["response_bytes"] = response_path.stat().st_size
                    row["seconds"] = time.time() - row["started"]
                    self.save()
                    if self.trace:
                        usage = row.get("usage", {})
                        fields = {"iteration": number, "model_id": alias, **{k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
                        self.trace.record("api_request", fields)
                    if accounting_error:
                        raise accounting_error
            finally:
                if run_token is not None:
                    try:
                        try:
                            if row is not None and not request_started:
                                row.update(course_price=0., actual_cost=0., inference_forwarded=False)
                                self.save()
                        finally:
                            self.run_control.settle(run_token, row.get("course_price_decimal", row.get("course_price")) if request_started else 0.)
                    except RunControlError as error:
                        raise AccountingError(str(error)) from error
            self._wait_retry(retry_delay)

    def _require_open(self):
        if self.run_control is not None:
            self.run_control.require_open()
            if self.task_id is not None:
                self.run_control.require_task(self.task_id)
        if getattr(self, 'effects_revoked', lambda: False)():
            raise InfrastructureError('Task effects revoked during provider retry; no further inference started')

    def _wait_retry(self, seconds):
        """Pace known non-generation attempts after settlement; shutdown stays live."""
        if self.run_control is None and not hasattr(self, 'effects_revoked'):
            time.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while True:
            self._require_open()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # Task revocation must also be noticed when the submission continues
            # other workers. Reuse the shared stop event rather than a retry thread.
            wait = min(remaining, .5) if hasattr(self, 'effects_revoked') else remaining
            if self.run_control is not None:
                self.run_control.stop_event.wait(wait)
            else:
                time.sleep(wait)


    @staticmethod
    def _account_response(row, response, prices, *, envelope=None):
        envelope = envelope or response_envelope(response)
        usage, provider = response_metadata(response, envelope=envelope)
        if envelope.accounting_error:
            row.update(accounting_status="uncertain", accounting_reason=envelope.accounting_error)
            for key in ("course_price", "course_price_decimal", "actual_cost", "usage", "provider"):
                row.pop(key, None)
            return
        if not usage:
            row.update(accounting_status="absent", accounting_reason="Provider omitted complete usage")
            return
        row.update(usage=usage, provider=provider)
        cost = usage.get("cost")
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                raise AccountingError("Invalid actual provider cost receipt")
            row["actual_cost"] = cost
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            row["course_price_decimal"] = str(course_cost(usage, prices, exact=True))
            row["course_price"] = float(row["course_price_decimal"])
        row.update(accounting_status="verified" if "course_price" in row else "incomplete",
                   accounting_reason="Independent receipt attributed to this response" if "course_price" in row
                   else "Receipt does not contain complete course token usage")
