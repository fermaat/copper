"""
Adapter: wraps core-llm-bridge's BridgeEngine to implement copper's LLMBase.
Install the [llm] extra to use this: `pdm install -G llm`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generator

from copper.llm.base import LLMBase, LLMResponse, Message

if TYPE_CHECKING:
    from core_llm_bridge import BridgeEngine


class BridgeAdapter(LLMBase):
    """Thin adapter so copper talks to core-llm-bridge."""

    def __init__(self, engine: "BridgeEngine"):
        self._engine = engine

    def complete(self, messages: list[Message], **kwargs) -> LLMResponse:
        # Set system prompt if present
        system = next((m.content for m in messages if m.role == "system"), None)
        if system:
            self._engine.set_system_prompt(system)

        user_messages = [m for m in messages if m.role != "system"]
        if not user_messages:
            return LLMResponse(text="")

        # Only send the last user message; history is managed by BridgeEngine
        user_input = user_messages[-1].content
        response = self._engine.chat(user_input)
        # Clear history after each call — copper passes full context in every prompt,
        # so accumulated history would only bloat subsequent requests with stale data.
        self._engine.clear_history()

        return LLMResponse(
            text=response.text,
            tokens_used=response.tokens_used or 0,
            cost_usd=response.cost_usd or 0.0,
            metadata=response.metadata or {},
        )

    def stream(self, messages: list[Message], **kwargs) -> Generator[str, None, None]:
        system = next((m.content for m in messages if m.role == "system"), None)
        if system:
            self._engine.set_system_prompt(system)

        user_messages = [m for m in messages if m.role != "system"]
        if not user_messages:
            return

        user_input = user_messages[-1].content
        for chunk in self._engine.chat_stream(user_input):
            yield chunk.text
