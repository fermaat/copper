"""Protocol + result type for retrievers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind


@dataclass
class RetrievalResult:
    """Pages selected for the Phase-2 answering context, per mind.

    ``selected`` maps mind name → ordered list of page slugs.
    ``tokens_used`` and ``cost_usd`` track LLM usage during retrieval (0 for
    retrievers that don't call an LLM). ``metadata`` is free-form per-retriever
    info (useful for logging or testing).
    """

    selected: dict[str, list[str]]
    tokens_used: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


class Retriever(Protocol):
    """Chooses which wiki pages to load for answering a question."""

    def retrieve(self, question: str, minds: list["CopperMind"]) -> RetrievalResult: ...
