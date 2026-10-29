"""Reuse Investigator's recorded HTTP effects; classify Chat Completions explicitly."""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.api import Provider
from .types import Completion, CompletionKind, ToolCall


class HTTPProvider:
    """A transport per call keeps SQLite connections in the invoking thread.

    Configuration uses the existing A4_MODEL/A4_BASE_URL/A4_API_KEY environment.
    Chat Completions is explicit: other protocols are rejected rather than
    silently projecting their response shapes into a different protocol.
    """

    def __init__(self, root: Path, model: str | None = None, *, configuration: dict | None = None):
        self.root = Path(root)
        transport = Provider(self.root)
        try:
            self.model = model or transport.model
            self.explicit_configuration = configuration is not None
            self.config = self._configuration(transport) if configuration is None else self.validate_configuration(configuration)
            self.model = self.config['model']
            with (self.root / "provider-config.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                path = self.root / "provider-config.json"
                if path.exists():
                    if not self.explicit_configuration and json.loads(path.read_text()) != self.config:
                        raise ValueError("Provider configuration changed; resume requires the frozen model, protocol and endpoint")
                else:
                    temp = self.root / "provider-config.pending"
                    with temp.open("w") as out:
                        json.dump(self.config, out, indent=2)
                        out.flush()
                        os.fsync(out.fileno())
                    temp.replace(path)
        finally:
            transport.store.db.close()

    @staticmethod
    def validate_configuration(configuration: dict) -> dict:
        if not isinstance(configuration, dict) or set(configuration) != {'protocol', 'endpoint', 'model'}:
            raise ValueError('Provider configuration requires exactly protocol, endpoint and model (credentials stay external)')
        if configuration['protocol'] != 'chat_completions':
            raise ValueError('Unsupported provider transition: team harness requires chat_completions')
        if not isinstance(configuration['model'], str) or not configuration['model'].strip():
            raise ValueError('Provider model must be nonempty')
        if not isinstance(configuration['endpoint'], str):
            raise ValueError('Provider endpoint must be a URL')
        url = urlsplit(configuration['endpoint'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError('Provider endpoint requires HTTP(S) without embedded credentials, query or fragment')
        return {**copy.deepcopy(configuration), 'endpoint': configuration['endpoint'].rstrip('/')}

    def _configuration(self, transport: Provider) -> dict[str, Any]:
        if transport.protocol != "chat_completions":
            raise ValueError("Team harness currently requires A4_API_PROTOCOL=chat_completions")
        url = urlsplit(transport.url)
        if url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError("Use API-key authentication and a plain provider endpoint without embedded credentials, query or fragment")
        return dict(transport.route, model=self.model)

    def complete(self, request: dict[str, Any], *, deadline=None, cancelled=None,
                 recover_no_tools=False) -> dict[str, Any]:
        transport = Provider(self.root)
        try:
            if self.explicit_configuration:
                transport.url = self.config['endpoint']
                transport.model = self.model
                transport.protocol = self.config['protocol']
                transport.path = '/chat/completions'
                transport.route = copy.deepcopy(self.config)
            if self._configuration(transport) != self.config or request.get("model") != self.config["model"]:
                raise ValueError("Provider configuration changed after session initialization")
            result = transport.call({"request": request, "event": "team_model_call", "branch": "team"},
                deadline=deadline, cancelled=cancelled,
                retry_response=failed_generation if recover_no_tools and not request.get('tools') else None)
            if result.get("error_kind"):
                record = result["response"]
                body = record.get("response")
                if not isinstance(body, dict):
                    try:
                        body = json.loads(record.get("error_body", ""))
                    except (ValueError, TypeError):
                        body = {"error": {"message": record.get("error_body", ""), "type": result["error_kind"]}}
                if not isinstance(body, dict):
                    body = {"error": body}
                # The original HTTP bytes remain in context.sqlite3. Keep the
                # provider error intact and identify transport metadata separately.
                return {**body, "_teamharness_transport": {
                    "error_kind": result["error_kind"], "archive": str(transport.store.path),
                    "raw_response_ref": result.get("raw_response_ref", record.get("raw_response_ref")),
                    "transport_response_ref": result.get("transport_response_ref"), "http_status": record.get("status")}}
            return result["raw_response"]
        finally:
            transport.store.db.close()


def failed_generation(raw):
    """Explicit generation failure; malformed answers/refusals are not transient."""
    choices = raw.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return False
    message = choices[0].get('message')
    return (choices[0].get('finish_reason') == 'error' and isinstance(message, dict)
            and message.get('role') == 'assistant' and not message.get('tool_calls')
            and not message.get('refusal') and not raw.get('error') and not choices[0].get('error'))


def parse_completion(raw: dict[str, Any]) -> Completion:
    """No action is authorized by an incomplete or ambiguous response."""
    metadata = raw.get("_teamharness_transport") if isinstance(raw, dict) else None
    if isinstance(metadata, dict) and metadata.get("error_kind") == "context_capacity":
        return Completion(CompletionKind.CONTEXT_CAPACITY, {}, detail="Provider rejected the request because its context exceeds capacity")
    if isinstance(raw, dict) and raw.get("error") is not None:
        text = json.dumps(raw["error"], ensure_ascii=False).lower()
        if any(fragment in text for fragment in (
                "context_capacity", "context_length_exceeded", "maximum context length", "context window", "input too long")):
            return Completion(CompletionKind.CONTEXT_CAPACITY, {}, detail="Provider rejected the request because its context exceeds capacity")
        return Completion(CompletionKind.ERROR, {}, detail="Provider error response")
    if not isinstance(raw, dict):
        return Completion(CompletionKind.ERROR, {}, detail="Provider error response")
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return Completion(CompletionKind.ERROR, {}, detail="Expected exactly one completion choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return Completion(CompletionKind.ERROR, {}, detail="Expected an assistant message")
    message = copy.deepcopy(message)
    reason = choice.get("finish_reason")
    if reason == "length":
        return Completion(CompletionKind.TRUNCATED, message, detail="Provider output limit interrupted this response")
    if reason == "content_filter" or message.get("refusal"):
        return Completion(CompletionKind.REFUSAL, message, detail="Provider refusal")
    if reason not in ("stop", "tool_calls"):
        return Completion(CompletionKind.ERROR, message, detail=f"Unrecognized completion status: {reason!r}")
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        return Completion(CompletionKind.ERROR, message, detail="tool_calls must be a list")
    parsed = []
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            return Completion(CompletionKind.ERROR, message, detail="Invalid function tool call")
        function = call.get("function")
        if not isinstance(function, dict):
            return Completion(CompletionKind.ERROR, message, detail="Missing tool function")
        ident, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
        if not all(isinstance(x, str) and x for x in (ident, name, arguments)):
            return Completion(CompletionKind.ERROR, message, detail="Missing tool identity, name or arguments")
        try:
            from .contracts import parse_arguments
            value = parse_arguments(arguments)
        except json.JSONDecodeError as error:
            return Completion(CompletionKind.ERROR, message,
                detail=f"Malformed tool argument JSON for {name}: {error.msg} at line {error.lineno}, column {error.colno}; no tools executed")
        except ValueError as error:
            return Completion(CompletionKind.ERROR, message, detail=f"Invalid tool argument JSON for {name}: {error}; no tools executed")
        if not isinstance(value, dict):
            return Completion(CompletionKind.ERROR, message, detail="Tool arguments must be an object")
        parsed.append(ToolCall(ident, name, arguments))
    if len({c.identifier for c in parsed}) != len(parsed):
        return Completion(CompletionKind.ERROR, message, detail="Duplicate tool call identities")
    if parsed:
        if reason != "tool_calls":
            return Completion(CompletionKind.ERROR, message, detail="Tool calls without tool_calls completion status")
        return Completion(CompletionKind.TOOLS, message, tuple(parsed))
    content = message.get("content")
    if reason == "tool_calls":
        return Completion(CompletionKind.ERROR, message, detail="Tool-call status without tool calls")
    if isinstance(content, str) and content.strip():
        return Completion(CompletionKind.ANSWER, message)
    return Completion(CompletionKind.EMPTY, message, detail="Completed response contained neither an answer nor tool calls")
