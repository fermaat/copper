"""Compose multiple retrievers. The first retriever has priority on ordering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.retrieval.base import RetrievalResult, Retriever

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind


@dataclass
class HybridRetriever:
    """Runs a pipeline of retrievers and merges their results.

    Each retriever runs in order. Slugs from earlier retrievers take priority
    (they appear first in the final list). Later retrievers fill remaining
    slots up to ``max_total_per_mind``.
    """

    retrievers: list[Retriever]
    max_total_per_mind: int = 20

    def retrieve(self, question: str, minds: list["CopperMind"]) -> RetrievalResult:
        merged: dict[str, list[str]] = {m.name: [] for m in minds}
        total_tokens = 0
        total_cost = 0.0
        metadata: dict = field(default_factory=dict) if False else {}

        for idx, retriever in enumerate(self.retrievers):
            result = retriever.retrieve(question, minds)
            total_tokens += result.tokens_used
            total_cost += result.cost_usd
            metadata[f"stage_{idx}_{type(retriever).__name__}"] = result.metadata

            for mind_name, slugs in result.selected.items():
                existing = {s.lower() for s in merged[mind_name]}
                added: list[str] = []
                for slug in slugs:
                    if len(merged[mind_name]) >= self.max_total_per_mind:
                        break
                    if slug.lower() in existing:
                        continue
                    merged[mind_name].append(slug)
                    existing.add(slug.lower())
                    added.append(slug)
                if added and idx > 0:
                    logger.info(
                        f"[retrieval.hybrid] {mind_name}: "
                        f"stage {idx} ({type(retriever).__name__}) added {len(added)} → {added}"
                    )

        return RetrievalResult(
            selected=merged,
            tokens_used=total_tokens,
            cost_usd=total_cost,
            metadata=metadata,
        )
