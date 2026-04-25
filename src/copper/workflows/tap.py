"""
Tap workflow — extract knowledge from the coppermind.

Reads the wiki (compiled knowledge) to answer a question.
Supports querying one or multiple copperminds simultaneously.

Page selection is delegated to a :class:`Retriever` from ``copper.retrieval``
so future strategies (BM25, embeddings, re-rankers) plug in without touching
this workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings
from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, LLMResponse, Message
from copper.prompts import render_prompt
from copper.retrieval import Retriever, build_default_retriever

if TYPE_CHECKING:
    pass


# Default personality name used when nothing is set per-mind or per-request.
# Actual text is loaded from the YAML identified by this name.
DEFAULT_TAP_PERSONALITY = "tap.archivist"


class TapWorkflow:
    """Answers a question using the compiled wiki."""

    def __init__(
        self,
        minds: list[CopperMind],
        llm: LLMBase,
        retriever: Retriever | None = None,
        personality: str | None = None,
    ):
        self.minds = minds
        self.llm = llm
        # Default retriever uses the same LLM for Phase 1 + keyword augmentation,
        # configured from Settings. Callers can inject a custom retriever for tests
        # or for future BM25/embedding pipelines.
        self.retriever = retriever or build_default_retriever(llm)
        # Personality drives which system prompt is used for the final answer.
        # Resolution: explicit arg → per-mind config → settings → fallback.
        self.personality = self._resolve_personality(personality)

    def _resolve_personality(self, override: str | None) -> str:
        if override:
            return override
        # Per-mind override: if all minds agree, use it; otherwise fall back to
        # global settings (ambiguous which mind's personality to honour).
        per_mind = {getattr(m.config, "tap_personality", "") for m in self.minds} - {""}
        if len(per_mind) == 1:
            return per_mind.pop()
        return settings.copper_tap_personality or DEFAULT_TAP_PERSONALITY

    def run(
        self,
        question: str,
        history: list[Message] | None = None,
        save_to_outputs: bool = False,
    ) -> TapResult:
        mind_names = ", ".join(m.name for m in self.minds)
        logger.info(
            f"[tap] Question: '{question[:80]}' | minds: [{mind_names}] "
            f"| personality: {self.personality}"
            + (f" | history: {len(history)} turns" if history else "")
        )

        # Phase 1 — assay: determine which pages to read.
        # When history is present, prepend recent turns so the retriever can
        # resolve pronouns and co-references in the current question.
        retrieval_question = question
        if history:
            recent = " ".join(m.content for m in history[-4:])
            retrieval_question = f"{recent} {question}"
        logger.info("[tap] Assaying the mentecobre to find relevant pages...")
        retrieval = self.retriever.retrieve(retrieval_question, self.minds)
        for mind_name, slugs in retrieval.selected.items():
            logger.info(f"[tap] Assay result [{mind_name}]: {len(slugs)} pages → {slugs}")

        total_tokens = retrieval.tokens_used
        total_cost = retrieval.cost_usd

        # Phase 2: build context from selected pages and answer the question.
        # Wiki context is injected into the current user message only — prior
        # turns in history carry just raw Q&A to keep token usage lean.
        context = _build_context(self.minds, retrieval.selected)
        multi = len(self.minds) > 1
        prompt = _build_tap_prompt(context, question, multi=multi)

        logger.info(
            f"[tap] Forging answer: context {len(context):,} chars | prompt {len(prompt):,} chars"
        )
        try:
            tap_system = render_prompt(self.personality)
        except ValueError:
            logger.warning(
                f"[tap] Personality '{self.personality}' not found, falling back to "
                f"'{DEFAULT_TAP_PERSONALITY}'"
            )
            tap_system = render_prompt(DEFAULT_TAP_PERSONALITY)
        messages = [Message(role="system", content=tap_system)]
        if history:
            messages.extend(history)
        messages.append(Message(role="user", content=prompt))
        logger.info(f"[tap] Sending to LLM ({len(messages)} messages):")
        for i, msg in enumerate(messages):
            preview = msg.content[:120].replace("\n", "↵")
            logger.info(f"[tap]   [{i}] {msg.role}: {preview!r} ({len(msg.content)} chars)")
        response = self.llm.complete(messages)
        total_tokens += response.tokens_used
        total_cost += response.cost_usd
        logger.info(f"[tap] LLM responded ({response.tokens_used} tokens)")
        logger.info(f"[tap] Answer preview: {response.text[:300].replace(chr(10), ' ')}")

        connections = _extract_connections(response.text)

        saved_to: list[Path] = []
        if save_to_outputs:
            saved_to = _save_to_outputs(question, response, self.minds)

        for mind in self.minds:
            mind.append_log("tap", f"Consulta: {question[:80]}")

        return TapResult(
            question=question,
            answer=response.text,
            minds_used=[m.name for m in self.minds],
            tokens_used=total_tokens,
            cost_usd=total_cost,
            saved_to=saved_to,
            connections=connections,
        )


def _build_context(minds: list[CopperMind], selected: dict[str, list[str]]) -> str:
    """Build context loading only the pages selected by the retriever."""
    parts: list[str] = []
    for mind in minds:
        wiki = WikiManager(mind.wiki_dir)
        index = wiki.read_index()
        slugs = selected.get(mind.name, [])

        parts.append(f"## Mentecobre: {mind.name} (tema: {mind.config.topic})")
        parts.append(f"### Índice\n{index}")

        for slug in slugs:
            page = wiki.page(slug)
            if page.exists():
                parts.append(f"### Página: {page.name}\n{page.raw}")
            else:
                logger.warning(f"[tap] Page '{slug}' selected but not found in '{mind.name}'")

        if not slugs:
            # Fallback: no pages selected — include all (rare, but safe)
            logger.warning(f"[tap] No pages selected for '{mind.name}', falling back to full wiki")
            for page in wiki.all_pages():
                parts.append(f"### Página: {page.name}\n{page.raw}")

    return "\n\n".join(parts)


def _build_tap_prompt(context: str, question: str, multi: bool = False) -> str:
    cross_mind_instructions = (
        (
            "\n\nYou are consulting MULTIPLE copperminds. In addition to answering, actively look for:\n"
            "- Concepts shared across different minds\n"
            "- Contradictions or tensions between them\n"
            "- Non-obvious connections that enrich the answer\n"
            "Mark them as: [Connection: mind-a ↔ mind-b: brief description]\n"
        )
        if multi
        else ""
    )

    return f"""\
## Wiki content
{context}
---
## Question
{question}{cross_mind_instructions}
Answer based solely on the wiki content above.
Cite the pages you use with [Source: page-name].
"""


def _extract_connections(text: str) -> list[str]:
    """Parse [Connection: ...] markers from the LLM response."""
    import re

    return re.findall(r"\[Connection:[^\]]+\]", text)


def _save_to_outputs(question: str, response: LLMResponse, minds: list[CopperMind]) -> list[Path]:
    from datetime import datetime

    date = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = question[:40].lower().replace(" ", "-").replace("?", "")
    filename = f"{date}_{slug}.md"

    content = f"# {question}\n\n{response.text}\n"
    saved: list[Path] = []

    for mind in minds:
        out_path = mind.outputs_dir / filename
        out_path.write_text(content)
        saved.append(out_path)

    return saved


class TapResult:
    def __init__(
        self,
        question: str,
        answer: str,
        minds_used: list[str],
        tokens_used: int,
        saved_to: list[Path],
        connections: list[str] | None = None,
        cost_usd: float = 0.0,
    ):
        self.question = question
        self.answer = answer
        self.minds_used = minds_used
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd
        self.saved_to = saved_to
        self.connections = connections or []

    def __repr__(self) -> str:
        return (
            f"TapResult(minds={self.minds_used}, "
            f"connections={len(self.connections)}, "
            f"tokens={self.tokens_used}, cost=${self.cost_usd:.6f}, saved={len(self.saved_to)})"
        )
