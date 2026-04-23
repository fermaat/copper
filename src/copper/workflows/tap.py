"""
Tap workflow — extract knowledge from the coppermind.

Reads the wiki (compiled knowledge) to answer a question.
Supports querying one or multiple copperminds simultaneously.
"""

from __future__ import annotations

from pathlib import Path

from core_utils.logger import logger

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, LLMResponse, Message

TAP_SYSTEM = """\
You are the Archivist of one or more copperminds. You answer questions based
exclusively on the compiled wiki content. Cite the wiki pages that inform
your answer with [Source: page-name].

Give thorough, detailed answers. Synthesize information across multiple pages
when relevant. Do not truncate or summarise unnecessarily.

When consulting multiple copperminds:
- Actively look for connections, parallels, or contradictions between them.
- Mark found connections with [Connection: mind-a ↔ mind-b: description].
- If the question only applies to some minds, state this clearly.

If your answer reveals new insights, offer to save them to the wiki.
"""

_SELECT_SYSTEM = """\
You are a wiki librarian. Given a question and a wiki index, identify which pages
contain information relevant to answering the question.
Return ONLY a list of page slugs — one per line, prefixed with "PAGE: ".
Do not answer the question. Do not explain your choices. Just list the slugs.
"""

# Maximum pages Phase 1 LLM can pick. Keeps context bounded.
_MAX_PAGES = 12

# Hard ceiling on total pages sent to Phase 2 after keyword augmentation.
_MAX_PAGES_TOTAL = 20

# Tiny stopword list for keyword extraction — short words are already excluded by length.
_STOPWORDS = {
    "what", "when", "where", "which", "while", "with", "this", "that",
    "they", "them", "their", "there", "then", "than", "have", "has",
    "been", "does", "doing", "done", "from", "into", "some", "other",
    "such", "more", "most", "many", "much", "each", "about", "against",
    "between", "through", "during", "before", "after", "above", "below",
    "here", "should", "would", "could", "must", "will", "shall",
    "how", "why", "who", "whose", "whom",
}


class TapWorkflow:
    """Answers a question using the compiled wiki."""

    def __init__(self, minds: list[CopperMind], llm: LLMBase):
        self.minds = minds
        self.llm = llm

    def run(self, question: str, save_to_outputs: bool = False) -> TapResult:
        mind_names = ", ".join(m.name for m in self.minds)
        logger.info(f"[tap] Question: '{question[:80]}' | minds: [{mind_names}]")

        total_tokens = 0
        total_cost = 0.0

        # Phase 1: select relevant pages by scanning indexes only
        logger.info("[tap] Phase 1: selecting relevant pages from indexes...")
        selected: dict[str, list[str]] = {}  # mind_name → [slug, ...]
        for mind in self.minds:
            wiki = WikiManager(mind.wiki_dir)
            index = wiki.read_index()
            slugs, sel_tokens, sel_cost = _select_pages(index, mind.name, question, self.llm)
            total_tokens += sel_tokens
            total_cost += sel_cost
            logger.info(f"[tap] Phase 1 [{mind.name}]: LLM picked {len(slugs)} → {slugs}")

            # Safety net: add pages whose slug/title literally matches question keywords.
            augmented = _augment_with_keywords(wiki, question, slugs, _MAX_PAGES_TOTAL)
            added = augmented[len(slugs):]
            if added:
                logger.info(f"[tap] Phase 1 [{mind.name}]: keyword augment added {len(added)} → {added}")
            selected[mind.name] = augmented

        # Phase 2: build context from selected pages and answer the question
        context = _build_context(self.minds, selected)
        multi = len(self.minds) > 1
        prompt = _build_tap_prompt(context, question, multi=multi)

        logger.info(f"[tap] Phase 2: context {len(context):,} chars | prompt {len(prompt):,} chars")
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


def _extract_keywords(question: str) -> list[str]:
    """Extract content-bearing keywords from a question for slug matching.

    Lowercases, splits on non-word chars, drops short words and stopwords,
    and applies a very simple plural stem so 'characters' matches 'character'.
    """
    import re

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


def _augment_with_keywords(
    wiki: WikiManager, question: str, selected_slugs: list[str], max_total: int
) -> list[str]:
    """Add pages whose slug or title contains any keyword from the question.

    LLM-selected slugs keep priority; keyword matches fill remaining slots
    up to ``max_total``. Returns the combined list.
    """
    keywords = _extract_keywords(question)
    if not keywords:
        return selected_slugs

    already = {s.lower() for s in selected_slugs}
    extra: list[str] = []

    for page in wiki.all_pages():
        if len(selected_slugs) + len(extra) >= max_total:
            break
        slug_lc = page.name.lower()
        if slug_lc in already:
            continue
        title_lc = (page.frontmatter.get("title") or "").lower() if page.exists() else ""
        haystack = slug_lc + " " + title_lc
        if any(kw in haystack for kw in keywords):
            extra.append(page.name)
            already.add(slug_lc)

    return selected_slugs + extra


def _select_pages(
    index: str, mind_name: str, question: str, llm: LLMBase
) -> tuple[list[str], int, float]:
    """Phase 1: ask the LLM to pick relevant page slugs from the index."""
    prompt = f"""\
## Wiki index for: {mind_name}
{index}

## Question
{question}

List the slugs of ALL pages that contain information relevant to answering this question.
One slug per line, prefixed with "PAGE: ". Include every page that could contribute
to a thorough answer — err on the side of including more rather than fewer.
Hard limit: {_MAX_PAGES} pages.
"""
    messages = [
        Message(role="system", content=_SELECT_SYSTEM),
        Message(role="user", content=prompt),
    ]
    try:
        response = llm.complete(messages)
    except Exception as exc:
        logger.warning(f"[tap] Phase 1 selection failed for '{mind_name}': {exc}")
        return [], 0, 0.0

    slugs = []
    for line in response.text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("PAGE:"):
            slug = stripped[5:].strip()
            if slug:
                slugs.append(slug)

    return slugs[:_MAX_PAGES], response.tokens_used, response.cost_usd


def _build_context(minds: list[CopperMind], selected: dict[str, list[str]]) -> str:
    """Build context loading only the pages selected in phase 1."""
    parts: list[str] = []
    for mind in minds:
        wiki = WikiManager(mind.wiki_dir)
        index = wiki.read_index()
        slugs = selected.get(mind.name, [])

        parts.append(f"## Mentecobre: {mind.name} (tema: {mind.config.topic})")
        parts.append(f"### Índice\n{index}")

        loaded = 0
        for slug in slugs:
            page = wiki.page(slug)
            if page.exists():
                parts.append(f"### Página: {page.name}\n{page.raw}")
                loaded += 1
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
