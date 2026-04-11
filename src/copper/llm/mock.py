"""
MockLLM — deterministic fake LLM for tests and offline development.

The Archivists of the Physical Realm practice on copper replicas before
handling the real metalminds.
"""

from __future__ import annotations

from copper.llm.base import LLMBase, LLMResponse, Message


class MockLLM(LLMBase):
    """Returns scripted or generated responses without any API call."""

    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self._call_count = 0
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message], **kwargs) -> LLMResponse:
        self.calls.append(messages)
        if self._responses:
            text = self._responses[self._call_count % len(self._responses)]
        else:
            # Auto-generate a plausible wiki response based on the last user message
            user_msg = next((m.content for m in reversed(messages) if m.role == "user"), "")
            text = self._auto_response(user_msg)

        self._call_count += 1
        return LLMResponse(text=text, tokens_used=len(text.split()), metadata={"mock": True})

    def _auto_response(self, prompt: str) -> str:
        if "index" in prompt.lower():
            return (
                "## Categorías\n\n"
                "### General\n"
                "- [[resumen-fuente]] — Resumen de la fuente procesada\n"
            )
        if "lint" in prompt.lower() or "polish" in prompt.lower():
            return (
                "# Informe de Salud\n\n"
                "🔵 INFO: Wiki en buen estado.\n"
                "🟡 AVISO: 0 páginas huérfanas detectadas.\n"
                "🔴 ERROR: 0 contradicciones encontradas.\n"
            )
        return (
            "## Resumen\n\n"
            "Contenido procesado y añadido al wiki.\n\n"
            "### Conceptos clave\n"
            "- Concepto A `[Fuente: fuente-procesada]`\n"
            "- Concepto B `[Fuente: fuente-procesada]`\n"
        )
