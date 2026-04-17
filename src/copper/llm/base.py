"""
LLM bridge interface for Copper.

Defines the minimal contract the Archivist needs from any LLM backend.
Plug in your own implementation (e.g. wrapping core-llm-bridge's BridgeEngine).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class LLMResponse:
    text: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMBase(ABC):
    """Minimal interface every LLM backend must implement."""

    @abstractmethod
    def complete(self, messages: list[Message], **kwargs) -> LLMResponse:
        """Single-shot completion."""
        ...

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        """Token-by-token streaming. Default: delegate to complete()."""
        response = self.complete(messages, **kwargs)
        yield response.text

    def chat(self, system_prompt: str, user_input: str, **kwargs) -> LLMResponse:
        """Convenience wrapper for simple system+user calls."""
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_input),
        ]
        return self.complete(messages, **kwargs)
