"""LLM-based retriever: asks the model to pick relevant slugs from the wiki index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt
from copper.retrieval.base import RetrievalResult

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind


_SELECT_SYSTEM_PROMPT = "assay.librarian"


@dataclass
class LLMRetriever:
    """Asks the LLM which page slugs look relevant to the question."""

    llm: LLMBase
    max_pages: int

    def retrieve(self, question: str, minds: list["CopperMind"]) -> RetrievalResult:
        selected: dict[str, list[str]] = {}
        total_tokens = 0
        total_cost = 0.0

        for mind in minds:
            wiki = WikiManager(mind.wiki_dir)
            index = wiki.read_index()
            slugs, tokens, cost = self._select_pages(index, mind.name, question)
            total_tokens += tokens
            total_cost += cost
            selected[mind.name] = slugs
            logger.info(f"[assay.llm] {mind.name}: picked {len(slugs)} → {slugs}")

        return RetrievalResult(selected=selected, tokens_used=total_tokens, cost_usd=total_cost)

    def _select_pages(
        self, index: str, mind_name: str, question: str
    ) -> tuple[list[str], int, float]:
        prompt = render_prompt(
            "assay.user",
            mind_name=mind_name,
            index=index,
            question=question,
            max_pages=self.max_pages,
        )
        messages = [
            Message(role="system", content=render_prompt(_SELECT_SYSTEM_PROMPT)),
            Message(role="user", content=prompt),
        ]
        try:
            response = self.llm.complete(messages)
        except Exception as exc:
            logger.warning(f"[assay.llm] Selection failed for '{mind_name}': {exc}")
            return [], 0, 0.0

        slugs = []
        for line in response.text.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("PAGE:"):
                slug = stripped[5:].strip()
                if slug:
                    slugs.append(slug)

        return slugs[: self.max_pages], response.tokens_used, response.cost_usd
