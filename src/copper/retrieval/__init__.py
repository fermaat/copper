"""Page retrieval layer — also called the *assay* in copper terminology.

In metallurgy, an assay determines what's inside an ore. Here, an assay
determines which wiki pages contain the information worth reading to
answer a question.

A ``Retriever`` takes a question and a list of copperminds and returns the
slugs of wiki pages worth loading into the Phase-2 answering context.

Implementations:
- :class:`LLMRetriever` — asks the LLM to pick slugs from the wiki index.
- :class:`KeywordRetriever` — adds pages whose slug/title literally matches
  keywords from the question.
- :class:`AlloyRetriever` — composes multiple retrievers, dedupes, caps.

Future implementations could include BM25, embeddings, or a re-ranker
without touching the Tap workflow.
"""

from copper.retrieval.alloy import AlloyRetriever
from copper.retrieval.base import RetrievalResult, Retriever
from copper.retrieval.factory import build_default_retriever
from copper.retrieval.keyword import KeywordRetriever
from copper.retrieval.llm import LLMRetriever

__all__ = [
    "Retriever",
    "RetrievalResult",
    "LLMRetriever",
    "KeywordRetriever",
    "AlloyRetriever",
    "build_default_retriever",
]
