"""LLM-based retriever: asks the model to pick relevant slugs from the wiki index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, Message
from copper.retrieval.base import RetrievalResult

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind


_SELECT_SYSTEM = """\
You are a wiki librarian. Given a question and a wiki index, identify which pages
contain information relevant to answering the question.
Return ONLY a list of page slugs — one per line, prefixed with "PAGE: ".
Do not answer the question. Do not explain your choices. Just list the slugs.
"""


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
            logger.info(f"[retrieval.llm] {mind.name}: picked {len(slugs)} → {slugs}")

        return RetrievalResult(selected=selected, tokens_used=total_tokens, cost_usd=total_cost)

    def _select_pages(
        self, index: str, mind_name: str, question: str
    ) -> tuple[list[str], int, float]:
        prompt = f"""\
## Wiki index for: {mind_name}
{index}

## Question
{question}

List the slugs of ALL pages that contain information relevant to answering this question.
One slug per line, prefixed with "PAGE: ". Include every page that could contribute
to a thorough answer — err on the side of including more rather than fewer.
Hard limit: {self.max_pages} pages.
"""
        messages = [
            Message(role="system", content=_SELECT_SYSTEM),
            Message(role="user", content=prompt),
        ]
        try:
            response = self.llm.complete(messages)
        except Exception as exc:
            logger.warning(f"[retrieval.llm] Selection failed for '{mind_name}': {exc}")
            return [], 0, 0.0

        slugs = []
        for line in response.text.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("PAGE:"):
                slug = stripped[5:].strip()
                if slug:
                    slugs.append(slug)

        return slugs[: self.max_pages], response.tokens_used, response.cost_usd
