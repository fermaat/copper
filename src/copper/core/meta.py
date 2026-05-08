"""Shared meta-summary helper used by PolishWorkflow and StoreWorkflow."""

from __future__ import annotations

from pathlib import Path

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt

_POLISH_SYSTEM_PROMPT = "polish.archivist"
_META_PROMPT = "polish.meta"


def regenerate_meta(mind: CopperMind, llm: LLMBase) -> Path:
    """Generate a fresh _meta.md for *mind* and write it to disk.

    Returns the path written. No-op (returns the path without writing) if the
    wiki is empty so that an empty mind never gets a misleading summary.
    """
    wiki = WikiManager(mind.wiki_dir)
    if not wiki.all_pages():
        return mind.meta_summary_path

    context = _build_meta_context(wiki)
    user_content = render_prompt(
        _META_PROMPT,
        mind_name=mind.name,
        topic=mind.config.topic,
        context=context,
    )
    messages = [
        Message(role="system", content=render_prompt(_POLISH_SYSTEM_PROMPT)),
        Message(role="user", content=user_content),
    ]
    text = llm.complete(messages).text
    mind.meta_summary_path.write_text(text)
    return mind.meta_summary_path


def _build_meta_context(wiki: WikiManager) -> str:
    parts: list[str] = [f"### Index\n{wiki.read_index()}"]
    for page in wiki.all_pages():
        parts.append(f"### {page.name}\n{page.raw}")
    return "\n\n".join(parts)
