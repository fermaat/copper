"""
Tap workflow — extract knowledge from the coppermind.

Reads the wiki (compiled knowledge) to answer a question.
Supports querying one or multiple copperminds simultaneously.

Page selection is delegated to a :class:`Retriever` from ``copper.retrieval``
so future strategies (BM25, embeddings, re-rankers) plug in without touching
this workflow.
"""

from __future__ import annotations

import re
from datetime import datetime
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

    def _gather_minds_and_pages(
        self,
        mind: CopperMind,
        question: str,
        depth: int,
    ) -> tuple[list[CopperMind], dict[str, list[str]], int, float]:
        """Return (minds, selected_slugs, tokens, cost) with scanner-guided recursion.

        Flat path (no children, or depth cap reached): delegates to the retriever.
        Hierarchical path: scanner chooses children, recurses into each.
        """
        max_depth = settings.copper_tap_max_depth

        if not mind.children() or depth >= max_depth:
            retrieval = self.retriever.retrieve(question, [mind])
            return [mind], retrieval.selected, retrieval.tokens_used, retrieval.cost_usd

        children = mind.children()
        children_info = [(c.name, c.meta_summary) for c in children]
        parent_context = _build_transversal_context(mind)

        logger.info(
            f"[tap] Hierarchical mind '{mind.name}' (depth {depth}): "
            f"scanning {len(children)} sub-copperminds..."
        )
        scanner_resp = self._call_scanner(parent_context, children_info, question)
        total_tokens = scanner_resp.tokens_used
        total_cost = scanner_resp.cost_usd

        selected_names = _parse_scanner_response(scanner_resp.text)
        logger.info(f"[tap] Scanner chose: {selected_names or ['(parent only)']}")

        parent_slugs = _transversal_slugs(mind)
        all_minds: list[CopperMind] = [mind]
        all_selected: dict[str, list[str]] = {mind.name: parent_slugs}

        if selected_names:
            child_map = {c.name: c for c in children}
            for name in selected_names:
                child = child_map.get(name)
                if child is None:
                    logger.warning(f"[tap] Scanner selected unknown child '{name}', skipping")
                    continue
                child_minds, child_selected, child_tokens, child_cost = (
                    self._gather_minds_and_pages(child, question, depth + 1)
                )
                all_minds.extend(child_minds)
                all_selected.update(child_selected)
                total_tokens += child_tokens
                total_cost += child_cost

        return all_minds, all_selected, total_tokens, total_cost

    def _call_scanner(
        self,
        parent_context: str,
        children_info: list[tuple[str, str]],
        question: str,
    ) -> "LLMResponse":
        children_block = "\n".join(
            f"- {name}: {summary or '(no meta summary yet)'}"
            for name, summary in children_info
        )
        user_content = (
            f"## Parent knowledge base overview\n{parent_context or '(empty)'}\n\n"
            f"## Available sub-copperminds\n{children_block}\n\n"
            f"## Question\n{question}"
        )
        messages = [
            Message(role="system", content=render_prompt("tap.scanner")),
            Message(role="user", content=user_content),
        ]
        return self.llm.complete(messages)

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
        # Hierarchical path: single root mind with sub-copperminds uses scanner + recursion.
        # Linked minds (expand_with_links) are orthogonal to parent/child — kept flat.
        if len(self.minds) == 1 and self.minds[0].children():
            context_minds, selected, total_tokens, total_cost = (
                self._gather_minds_and_pages(self.minds[0], retrieval_question, depth=0)
            )
        else:
            retrieval = self.retriever.retrieve(retrieval_question, self.minds)
            context_minds = self.minds
            selected = retrieval.selected
            total_tokens = retrieval.tokens_used
            total_cost = retrieval.cost_usd

        for mind_name, slugs in selected.items():
            logger.info(f"[tap] Assay result [{mind_name}]: {len(slugs)} pages → {slugs}")

        # Phase 2: build context from selected pages and answer the question.
        # Wiki context is injected into the current user message only — prior
        # turns in history carry just raw Q&A to keep token usage lean.
        context = _build_context(context_minds, selected)
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


_TRANSVERSAL_EXCLUDE = frozenset({"index.md", "log.md", "_meta.md"})


def _parse_scanner_response(text: str) -> list[str]:
    """Extract child names from a scanner <descend>...</descend> block."""
    match = re.search(r"<descend>(.*?)</descend>", text, re.DOTALL)
    if not match:
        return []
    return [n.strip() for n in match.group(1).splitlines() if n.strip()]


def _transversal_slugs(mind: CopperMind) -> list[str]:
    """Slugs of the parent's own wiki pages (excludes meta, index, log, reports)."""
    if not mind.wiki_dir.exists():
        return []
    return [
        p.stem
        for p in sorted(mind.wiki_dir.glob("*.md"))
        if p.name not in _TRANSVERSAL_EXCLUDE and not p.name.startswith("lint-report")
    ]


def _build_transversal_context(mind: CopperMind) -> str:
    """Build a text block from the parent's own wiki pages for the scanner."""
    slugs = _transversal_slugs(mind)
    if not slugs:
        return ""
    parts = []
    for slug in slugs:
        path = mind.wiki_dir / f"{slug}.md"
        if path.exists():
            parts.append(f"### {slug}\n{path.read_text()}")
    return "\n\n".join(parts)


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
    return render_prompt(
        "tap.user",
        context=context,
        question=question,
        cross_mind_instructions=cross_mind_instructions,
    )


def _extract_connections(text: str) -> list[str]:
    """Parse [Connection: ...] markers from the LLM response."""
    return re.findall(r"\[Connection:[^\]]+\]", text)


def _save_to_outputs(question: str, response: LLMResponse, minds: list[CopperMind]) -> list[Path]:
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
