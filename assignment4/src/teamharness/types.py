"""Immutable semantic boundaries; JSON appears only at external interfaces."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol


class CompletionKind(str, Enum):
    ANSWER = "answer"
    TOOLS = "tools"
    TRUNCATED = "truncated"
    EMPTY = "empty"
    REFUSAL = "refusal"
    ERROR = "error"
    CONTEXT_CAPACITY = "context_capacity"


@dataclass(frozen=True)
class ToolCall:
    identifier: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class Completion:
    kind: CompletionKind
    message: dict[str, Any]
    calls: tuple[ToolCall, ...] = ()
    detail: str = ""


class ProviderProtocol(Protocol):
    model: str

    def complete(self, request: dict[str, Any]) -> dict[str, Any]: ...


ToolExecutor = Callable[[str, dict[str, Any], str], dict[str, Any]]
