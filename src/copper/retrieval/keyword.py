"""Keyword-based retriever: matches question keywords against page slugs/titles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.core.wiki import WikiManager
from copper.retrieval.base import RetrievalResult

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind


# Small stopword list. Short words (<4 chars) are already excluded by the regex below.
_STOPWORDS = {
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "this",
    "that",
    "they",
    "them",
    "their",
    "there",
    "then",
    "than",
    "have",
    "has",
    "been",
    "does",
    "doing",
    "done",
    "from",
    "into",
    "some",
    "other",
    "such",
    "more",
    "most",
    "many",
    "much",
    "each",
    "about",
    "against",
    "between",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "here",
    "should",
    "would",
    "could",
    "must",
    "will",
    "shall",
    "how",
    "why",
    "who",
    "whose",
    "whom",
}


def extract_keywords(question: str) -> list[str]:
    """Lowercase, tokenise, drop short words / stopwords, and simple-stem plurals."""
    stems: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-zA-Z]{4,}", question.lower()):
        if raw in _STOPWORDS:
            continue
        if raw.endswith("ies") and len(raw) > 4:
            stem = raw[:-3] + "y"
        elif raw.endswith("s") and not raw.endswith("ss") and len(raw) > 4:
            stem = raw[:-1]
        else:
            stem = raw
        if stem not in seen:
            seen.add(stem)
            stems.append(stem)
    return stems


@dataclass
class KeywordRetriever:
    """Adds pages whose slug or title literally contains a question keyword.

    Intended to be composed (e.g. via :class:`HybridRetriever`) with an
    LLM-based retriever as a safety net for slugs the LLM overlooked.
    """

    max_pages_per_mind: int = 20

    def retrieve(self, question: str, minds: list["CopperMind"]) -> RetrievalResult:
        keywords = extract_keywords(question)
        selected: dict[str, list[str]] = {}

        if not keywords:
            for mind in minds:
                selected[mind.name] = []
            return RetrievalResult(selected=selected, metadata={"keywords": []})

        for mind in minds:
            wiki = WikiManager(mind.wiki_dir)
            matches: list[str] = []
            for page in wiki.all_pages():
                if len(matches) >= self.max_pages_per_mind:
                    break
                haystack = page.name.lower()
                title = (page.frontmatter.get("title") or "").lower() if page.exists() else ""
                if title:
                    haystack = haystack + " " + title
                if any(kw in haystack for kw in keywords):
                    matches.append(page.name)
            selected[mind.name] = matches
            logger.info(
                f"[assay.keyword] {mind.name}: {len(matches)} matches for {keywords} → {matches}"
            )

        return RetrievalResult(selected=selected, metadata={"keywords": keywords})
