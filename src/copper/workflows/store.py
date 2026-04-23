"""
Store workflow — fill the coppermind with knowledge.

The Archivist reads a source, extracts knowledge, and weaves it
into the existing wiki. Large sources are automatically split into
chunks so they fit within the model's context window.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings
from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.ingest.registry import default_registry
from copper.llm.base import LLMBase, Message

if TYPE_CHECKING:
    from copper.ingest.image_describer import ImageDescriber


# Maximum characters sent to the LLM per chunk. Override via COPPER_STORE_MAX_CHUNK_CHARS.
MAX_CHUNK_CHARS = settings.copper_store_max_chunk_chars

# Number of additional attempts after the first if the LLM returns no valid XML.
# 1 retry is usually enough; the second attempt uses a more emphatic prompt.
_MAX_XML_RETRIES = 1

# Hoisted so retry logic can quickly check "is there anything parseable here?"
# without the side effects of _apply_wiki_updates.
import re as _re

# action is optional — gemma4 and similar sometimes omit it for new pages.
# When missing we default to "create" in the consumer.
_PAGE_PATTERN = _re.compile(
    r'<page\s+slug="([^"]+)"\s+title="([^"]+)"(?:\s+action="([^"]+)")?[^>]*>\s*<content>(.*?)</content>\s*</page>',
    _re.DOTALL,
)

STORE_SYSTEM = """\
You are the Archivist of a coppermind (mentecobre). Your mission is to maintain a
structured markdown wiki. You read sources, extract knowledge, and weave it into the
existing wiki. You follow the coppermind's schema strictly. You never modify files in
raw/ — you only write to wiki/.

LANGUAGE RULE (important):
- Write the wiki content in the SAME LANGUAGE as the source material.
- If the source is in English, the wiki pages MUST be in English.
- If the source is in Spanish, the wiki pages MUST be in Spanish.
- Never translate during transcription — preserve the original language and nuance.
"""


class StoreWorkflow:
    """Processes a source file and updates the wiki."""

    def __init__(
        self,
        mind: CopperMind,
        llm: LLMBase,
        image_describer: "ImageDescriber | None" = None,
    ):
        self.mind = mind
        self.llm = llm
        self.image_describer = image_describer
        self.wiki = WikiManager(mind.wiki_dir)

    def run(self, source_path: Path) -> StoreResult:
        if not source_path.exists():
            raise FileNotFoundError(f"Fuente no encontrada: {source_path}")

        # Copy to raw/ if not already there
        raw_path = self.mind.raw_dir / source_path.name
        if source_path.resolve() != raw_path.resolve():
            import shutil

            shutil.copy2(source_path, raw_path)

        source_name = raw_path.name
        logger.info(f"[store] Extracting text from '{source_name}'...")

        registry = default_registry()
        chunks = registry.to_chunks(
            raw_path,
            MAX_CHUNK_CHARS,
            llm=self.llm,
            image_describer=self.image_describer,
        )

        char_count = sum(len(c) for c in chunks)
        # Source is smelted into ingots — manageable chunks that fit the forge.
        total_ingots = len(chunks)
        logger.info(
            f"[store] '{source_name}' → {char_count:,} chars smelted into {total_ingots} ingot(s)"
        )

        schema = self.mind.schema()
        all_pages: list[str] = []
        total_tokens = 0
        total_cost = 0.0

        for i, chunk in enumerate(chunks, 1):
            ingot_label = f"ingot {i}/{total_ingots}" if total_ingots > 1 else None
            if ingot_label:
                logger.info(f"[store] Forging {ingot_label} ({len(chunk):,} chars)...")

            # Refresh index each iteration so the LLM sees pages created by previous ingots
            index_content = self.wiki.read_index()
            existing_slugs = [p.name for p in self.wiki.all_pages()]
            prompt = _build_store_prompt(
                schema,
                source_name,
                chunk,
                index_content,
                chunk_label=ingot_label,
                existing_slugs=existing_slugs,
            )

            logger.info(f"[store] Sending to LLM ({len(prompt):,} chars in prompt)...")
            response_text, attempt_tokens, attempt_cost = _send_with_retry(
                self.llm, STORE_SYSTEM, prompt
            )
            total_tokens += attempt_tokens
            total_cost += attempt_cost

            pages = _apply_wiki_updates(response_text, source_name, self.wiki)
            all_pages.extend(pages)
            logger.info(
                f"[store] Ingot {i}/{total_ingots} forged: {len(pages)} page(s) written → {pages}"
            )

        self.mind.append_log(
            "store",
            f"Fuente '{source_name}' almacenada → {len(all_pages)} páginas actualizadas",
        )
        logger.info(
            f"[store] Done: '{source_name}' → {len(all_pages)} pages, {total_tokens} tokens"
        )

        # After multi-ingot forging, run polish to consolidate duplicates and fix gaps
        if total_ingots > 1:
            logger.info(f"[store] Running consolidation polish ({len(all_pages)} wiki pages)...")
            from copper.workflows.polish import PolishWorkflow

            polish_result = PolishWorkflow(self.mind, self.llm).run()
            total_tokens += polish_result.tokens_used
            total_cost += polish_result.cost_usd
            logger.info(
                f"[store] Polish done → {len(polish_result.structural_issues)} structural issues, "
                f"report at {polish_result.report_path.name}"
            )

        return StoreResult(
            source=source_name,
            pages_written=all_pages,
            tokens_used=total_tokens,
            cost_usd=total_cost,
        )


def _build_store_prompt(
    schema: str,
    source_name: str,
    source_text: str,
    index: str,
    chunk_label: str | None = None,
    existing_slugs: list[str] | None = None,
) -> str:
    chunk_note = (
        f"\n> Note: this text is {chunk_label} of the document. "
        f"Integrate the knowledge with what already exists in the wiki.\n"
        if chunk_label
        else ""
    )

    update_note = ""
    if existing_slugs and chunk_label:
        slugs_str = ", ".join(existing_slugs)
        update_note = (
            f"\n## Pages already in the wiki ({len(existing_slugs)} total)\n{slugs_str}\n\n"
            "> IMPORTANT: Only touch pages DIRECTLY relevant to this fragment. "
            'Use action="update" if the page already exists; action="create" if not. '
            "Do not touch pages unrelated to this fragment.\n"
        )

    return f"""\
## Coppermind schema
{schema}

## Current wiki index
{index}
{update_note}
## New source to store: {source_name}{chunk_note}
{source_text}

---

Process this source following the schema's storage workflow.
Return the wiki updates in the following XML format:

<wiki_updates>
  <page slug="page-name" title="Page Title" action="create|update">
    <content>
    Full page content (without frontmatter — I add that myself).
    </content>
  </page>
  ... (repeat for each page to create or update)
  <index>
    Full content of the new index.md
  </index>
</wiki_updates>

Important rules:
- Include between 1 and 5 pages (only the most relevant for this fragment).
- Slug must be kebab-case and descriptive.
- Add [[backlinks]] where appropriate.
- Cite sources as [Source: {source_name}].
- Mark contradictions when present.
- PRESERVE `[Visual on page N: ...]` markers verbatim when they add useful visual
  information (colours, anatomy, scenes, diagrams). They are descriptions of
  figures from the original document and carry knowledge absent from the prose.
- LANGUAGE: write the wiki content in the SAME LANGUAGE as the source text
  above. Do not translate.
"""


def _send_with_retry(
    llm: LLMBase, system_prompt: str, user_prompt: str, max_retries: int = _MAX_XML_RETRIES
) -> tuple[str, int, float]:
    """Call the LLM; retry once with a stricter prompt if no valid <page> XML appears.

    Returns (final_text, total_tokens_across_all_attempts, total_cost).
    Token/cost are accumulated across attempts so the workflow stats stay honest.
    """
    accumulated_text = ""
    accumulated_tokens = 0
    accumulated_cost = 0.0

    for attempt in range(max_retries + 1):
        if attempt == 0:
            content = user_prompt
        else:
            content = (
                user_prompt
                + "\n\n---\n"
                + "IMPORTANT: Your previous response did not contain valid, parseable XML.\n"
                + "Respond now with ONLY the <wiki_updates>...</wiki_updates> structure.\n"
                + "Requirements (all mandatory):\n"
                + '- Every <page> tag MUST include all three attributes: slug="...", '
                + 'title="...", action="create" (or "update" if updating).\n'
                + "- Every <content> tag MUST be closed with </content> before </page>.\n"
                + "- Every <page> tag MUST be closed with </page>.\n"
                + "- The whole block MUST close with </wiki_updates>.\n"
                + "- Do NOT wrap the response in markdown code fences (```).\n"
                + "- Do NOT add preamble, commentary, or explanation.\n"
                + "- Keep each page body concise enough that the full XML fits in your output budget."
            )
            logger.info(f"[store] Retry {attempt}/{max_retries} with stricter prompt...")

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=content),
        ]
        response = llm.complete(messages)
        accumulated_tokens += response.tokens_used
        accumulated_cost += response.cost_usd
        accumulated_text = response.text
        logger.info(f"[store] LLM responded ({response.tokens_used} tokens, attempt {attempt + 1})")

        if _PAGE_PATTERN.search(response.text):
            return accumulated_text, accumulated_tokens, accumulated_cost

        if attempt < max_retries:
            preview = response.text.strip().replace("\n", " ")[:500]
            logger.warning(
                f"[store] Attempt {attempt + 1}: no valid XML. "
                f"Preview: {preview}{'…' if len(response.text) > 500 else ''}"
            )

    return accumulated_text, accumulated_tokens, accumulated_cost


def _apply_wiki_updates(llm_output: str, source_name: str, wiki: WikiManager) -> list[str]:
    """Parse <wiki_updates> XML from LLM output and write pages.

    By the time this is called, ``_send_with_retry`` has already exhausted its
    retries — so a missing XML structure here means we genuinely fall back.
    """
    import re

    pages_written: list[str] = []

    for m in _PAGE_PATTERN.finditer(llm_output):
        slug, title, action, content = (
            m.group(1),
            m.group(2),
            m.group(3) or "create",
            m.group(4).strip(),
        )
        wiki.upsert_page(
            slug=slug, title=title, body=content, bump_source_count=(action == "update")
        )
        pages_written.append(slug)

    index_match = re.search(r"<index>(.*?)</index>", llm_output, re.DOTALL)
    if index_match:
        wiki.update_index(index_match.group(1).strip())

    if not pages_written:
        # All retries exhausted. Dump a preview so the failure is debuggable.
        preview = llm_output.strip().replace("\n", " ")[:500]
        logger.warning(
            f"[store] No valid XML after retries — creating fallback summary page. "
            f"Raw output preview: {preview}{'…' if len(llm_output) > 500 else ''}"
        )
        slug = source_name.replace(".", "-").lower()
        wiki.upsert_page(slug=slug, title=f"Fallback: {source_name}", body=llm_output)
        pages_written.append(slug)

    return pages_written


class StoreResult:
    def __init__(
        self, source: str, pages_written: list[str], tokens_used: int, cost_usd: float = 0.0
    ):
        self.source = source
        self.pages_written = pages_written
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return f"StoreResult(source={self.source!r}, pages={len(self.pages_written)}, tokens={self.tokens_used}, cost=${self.cost_usd:.6f})"
