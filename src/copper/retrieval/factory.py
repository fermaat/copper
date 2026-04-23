"""Factory for the default retriever pipeline (LLM + keyword fallback)."""

from __future__ import annotations

from copper.config import settings
from copper.llm.base import LLMBase
from copper.retrieval.base import Retriever
from copper.retrieval.hybrid import HybridRetriever
from copper.retrieval.keyword import KeywordRetriever
from copper.retrieval.llm import LLMRetriever


def build_default_retriever(llm: LLMBase) -> Retriever:
    """Build the default retrieval pipeline from Settings.

    Stage 1: LLM picks from the index (up to ``copper_tap_max_pages``).
    Stage 2: keyword-augmentation fills up to ``copper_tap_max_pages_total``.
    """
    return HybridRetriever(
        retrievers=[
            LLMRetriever(llm=llm, max_pages=settings.copper_tap_max_pages),
            KeywordRetriever(max_pages_per_mind=settings.copper_tap_max_pages_total),
        ],
        max_total_per_mind=settings.copper_tap_max_pages_total,
    )
