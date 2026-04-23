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

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, LLMResponse, Message
from copper.retrieval import Retriever, build_default_retriever

if TYPE_CHECKING:
    pass


TAP_SYSTEM = """\
You are the Archivist of one or more copperminds. You answer questions based
exclusively on the compiled wiki content. Cite the wiki pages that inform
your answer with [Source: page-name].

Give thorough, detailed answers. Synthesize information across multiple pages
when relevant. Do not truncate or summarise unnecessarily.

Numerical precision rules (read carefully, the user WILL notice mistakes):
- When a rule says "at levels X, Y, Z", the event happens ONLY at those exact
  levels — not at every level in between. For a question about the transition
  "from N to M", check whether M (the level being reached) is one of the
  listed thresholds.
- When the wiki gives a table or list of specific values, quote them exactly;
  do not paraphrase numbers. If a question asks about a specific row/level/case,
  locate the corresponding row before answering.
- If the wiki does not contain a specific number or rule the question asks for,
  say so explicitly rather than inventing a plausible default.

When consulting multiple copperminds:
- Actively look for connections, parallels, or contradictions between them.
- Mark found connections with [Connection: mind-a ↔ mind-b: description].
- If the question only applies to some minds, state this clearly.

If your answer reveals new insights, offer to save them to the wiki.
"""


class TapWorkflow:
    """Answers a question using the compiled wiki."""

    def __init__(
        self,
        minds: list[CopperMind],
        llm: LLMBase,
        retriever: Retriever | None = None,
    ):
        self.minds = minds
        self.llm = llm
        # Default retriever uses the same LLM for Phase 1 + keyword augmentation,
        # configured from Settings. Callers can inject a custom retriever for tests
        # or for future BM25/embedding pipelines.
        self.retriever = retriever or build_default_retriever(llm)

    def run(self, question: str, save_to_outputs: bool = False) -> TapResult:
        mind_names = ", ".join(m.name for m in self.minds)
        logger.info(f"[tap] Question: '{question[:80]}' | minds: [{mind_names}]")

        # Phase 1 — assay: determine which pages of each mentecobre to read.
        logger.info("[tap] Assaying the mentecobre to find relevant pages...")
        retrieval = self.retriever.retrieve(question, self.minds)
        for mind_name, slugs in retrieval.selected.items():
            logger.info(f"[tap] Assay result [{mind_name}]: {len(slugs)} pages → {slugs}")

        total_tokens = retrieval.tokens_used
        total_cost = retrieval.cost_usd

        # Phase 2: build context from selected pages and answer the question
        context = _build_context(self.minds, retrieval.selected)
        multi = len(self.minds) > 1
        prompt = _build_tap_prompt(context, question, multi=multi)

        logger.info(
            f"[tap] Forging answer: context {len(context):,} chars | prompt {len(prompt):,} chars"
        )
        logger.info("[tap] Sending to LLM...")
        messages = [
            Message(role="system", content=TAP_SYSTEM),
            Message(role="user", content=prompt),
        ]
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
