"""
Store workflow — fill the coppermind with knowledge.

The Archivist reads a source, extracts knowledge, and weaves it
into the existing wiki. Large sources are automatically split into
chunks so they fit within the model's context window.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings
from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.ingest.registry import default_registry
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt

if TYPE_CHECKING:
    from copper.ingest.image_describer import ImageDescriber


# Maximum characters sent to the LLM per chunk. Override via COPPER_STORE_MAX_CHUNK_CHARS.
MAX_CHUNK_CHARS = settings.copper_store_max_chunk_chars

# Number of additional attempts after the first if the LLM returns no valid XML.
# 1 retry is usually enough; the second attempt uses a more emphatic prompt.
_MAX_XML_RETRIES = 1

# action is optional — gemma4 and similar sometimes omit it for new pages.
# When missing we default to "create" in the consumer.
_PAGE_PATTERN = re.compile(
    r'<page\s+slug="([^"]+)"\s+title="([^"]+)"(?:\s+action="([^"]+)")?[^>]*>\s*<content>(.*?)</content>\s*</page>',
    re.DOTALL,
)

# Visual marker emitted by the PDF ingest. Carries page+image coordinates and a
# short description with optional "(Keywords: …)" tail used by the safety net.
_VISUAL_MARKER_RE = re.compile(r"\[Visual on page \d+, image \d+:[^\]]+\]")
_VISUAL_MARKER_ID_RE = re.compile(r"\[Visual on page \d+, image \d+:")
_VISUAL_KEYWORDS_RE = re.compile(r"\((?:keywords|tags):\s*([^)]+)\)", re.IGNORECASE)
_VISUAL_PREFIX_RE = re.compile(r"^\[Visual on page \d+, image \d+:\s*")

# Words from the marker that carry no semantic signal — they appear in every
# marker by construction and would otherwise bias the scoring towards any page
# that already contains a visual.
_MARKER_STOPWORDS = frozenset(
    {"keywords", "tags", "visual", "image", "page", "pages"}
)

# Loaded lazily via render_prompt() inside the workflow so a missing YAML
# surfaces early with a clear error, rather than at module import time.
_STORE_SYSTEM_PROMPT = "store.archivist"


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
            shutil.copy2(source_path, raw_path)

        source_name = raw_path.name
        logger.info(f"[store] Extracting text from '{source_name}'...")

        registry = default_registry()
        # When multimodal is active and the image-save setting is on, persist
        # described images to <mind>/raw/images/ so the UI can render them.
        image_save_dir: Path | None = None
        if self.image_describer is not None and settings.copper_ingest_save_images:
            image_save_dir = self.mind.raw_dir / "images"

        chunks = registry.to_chunks(
            raw_path,
            MAX_CHUNK_CHARS,
            llm=self.llm,
            image_describer=self.image_describer,
            image_save_dir=image_save_dir,
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

            visual_count = chunk.count("[Visual on page")
            logger.info(
                f"[store] Sending to LLM ({len(prompt):,} chars in prompt"
                + (f", {visual_count} visual markers" if visual_count else "")
                + ")..."
            )

            # Snapshot pre-LLM bodies so the safety net can restore visual
            # markers the LLM may silently drop during action="update".
            pre_llm_bodies = {p.name: p.body for p in self.wiki.all_pages()}

            response_text, attempt_tokens, attempt_cost = _send_with_retry(
                self.llm, render_prompt(_STORE_SYSTEM_PROMPT), prompt
            )
            total_tokens += attempt_tokens
            total_cost += attempt_cost

            pages = _apply_wiki_updates(response_text, source_name, self.wiki)
            all_pages.extend(pages)

            # Restrict the snapshot to pages the LLM actually touched — the
            # only ones whose markers could have been lost by an update.
            existing_before = {
                slug: pre_llm_bodies[slug] for slug in pages if slug in pre_llm_bodies
            }
            _inject_missing_visual_markers(chunk, pages, self.wiki, existing_before)
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


# ---------------------------------------------------------------------- #
# Visual marker helpers                                                   #
# ---------------------------------------------------------------------- #


def _extract_visual_markers(text: str) -> list[str]:
    return _VISUAL_MARKER_RE.findall(text)


def _marker_id(marker: str) -> str:
    """Stable identifier '[Visual on page N, image M:' — used for dedup only."""
    m = _VISUAL_MARKER_ID_RE.match(marker)
    return m.group(0) if m else marker


def _marker_keywords(marker: str) -> list[str]:
    """Extract the comma-separated keyword list from '(Keywords: a, b, c)'."""
    m = _VISUAL_KEYWORDS_RE.search(marker)
    if not m:
        return []
    return [k.strip().lower() for k in m.group(1).split(",") if k.strip()]


def _marker_description_words(marker: str) -> list[str]:
    """Distinctive words from the description body, minus structural boilerplate.

    Skips the leading "[Visual on page N, image M:" prefix and the trailing
    "(Keywords: …)" tail, then drops short tokens and known boilerplate.
    """
    body = _VISUAL_PREFIX_RE.sub("", marker)
    body = _VISUAL_KEYWORDS_RE.sub("", body)
    body = body.rstrip("] ").strip().lower()
    return [w for w in re.findall(r"\w+", body) if len(w) > 4 and w not in _MARKER_STOPWORDS]


def _pick_best_slug(marker: str, bodies: dict[str, str]) -> str:
    """Pick the wiki slug most semantically related to a visual marker.

    Scoring (higher is better):
    - +100 if any marker keyword appears in the slug name itself.
    - +20 per marker keyword found in the page body.
    - +50 if the slug-as-words appears verbatim inside the marker.
    - +2 per distinctive description word found in the page body.

    Ties resolve to the first slug in iteration order.
    """
    kws = _marker_keywords(marker)
    desc_words = _marker_description_words(marker)
    marker_low = marker.lower()

    best_slug = next(iter(bodies))
    best_score = -1
    for slug, body in bodies.items():
        body_low = body.lower()
        slug_words = slug.replace("-", " ").lower()
        score = 0
        if any(k in slug_words for k in kws):
            score += 100
        score += sum(20 for k in kws if k in body_low)
        if slug_words and slug_words in marker_low:
            score += 50
        score += sum(2 for w in desc_words if w in body_low)
        if score > best_score:
            best_score = score
            best_slug = slug
    return best_slug


def _inject_missing_visual_markers(
    chunk: str,
    page_slugs: list[str],
    wiki: WikiManager,
    existing_before: dict[str, str] | None = None,
) -> None:
    """Safety net for visual markers after the LLM has written its pages.

    Two responsibilities, in order:
    1. Restore markers that existed in a page BEFORE the LLM update but
       disappeared from its rewritten body.
    2. Inject any chunk markers the LLM omitted entirely, scored against the
       touched pages.

    Each affected page is written at most once, regardless of how many
    markers were restored or injected on it.
    """
    if not page_slugs:
        return

    # Read the current (post-LLM) body of every touched page that exists.
    bodies: dict[str, str] = {}
    for slug in page_slugs:
        p = wiki.page(slug)
        if p.exists():
            bodies[slug] = p.body
    if not bodies:
        return

    dirty: set[str] = set()

    # 1. Restore markers the LLM dropped during an update.
    if existing_before:
        for slug, old_body in existing_before.items():
            if slug not in bodies:
                continue
            old_markers = _extract_visual_markers(old_body)
            if not old_markers:
                continue
            new_ids = {_marker_id(m) for m in _extract_visual_markers(bodies[slug])}
            lost = [m for m in old_markers if _marker_id(m) not in new_ids]
            if not lost:
                continue
            logger.info(
                f"[store] Restoring {len(lost)} previous marker(s) on '{slug}' "
                "lost during update"
            )
            bodies[slug] = bodies[slug].rstrip() + "\n\n" + "\n\n".join(lost) + "\n"
            dirty.add(slug)

    # 2. Inject orphan markers from the current chunk.
    chunk_markers = _extract_visual_markers(chunk)
    if chunk_markers:
        present_ids = {
            _marker_id(m) for body in bodies.values() for m in _extract_visual_markers(body)
        }
        for marker in chunk_markers:
            m_id = _marker_id(marker)
            if m_id in present_ids:
                continue
            best_slug = _pick_best_slug(marker, bodies)
            logger.info(f"[store] Injecting orphan marker {m_id} into '{best_slug}'")
            bodies[best_slug] = bodies[best_slug].rstrip() + "\n\n" + marker + "\n"
            dirty.add(best_slug)
            present_ids.add(m_id)

    # 3. Persist each modified page exactly once.
    for slug in dirty:
        wiki.update_page(slug, bodies[slug])


# ---------------------------------------------------------------------- #
# Prompt assembly + LLM IO                                                #
# ---------------------------------------------------------------------- #


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

    visual_markers = _extract_visual_markers(source_text)
    images_section = ""
    if visual_markers:
        markers_str = "\n".join(f"  {m}" for m in visual_markers)
        images_section = f"""
## Images in this fragment
The following image markers were extracted from this document fragment.
You MUST embed every marker in exactly one of the pages you write — the page whose
content is most closely related to the image. Copy each marker verbatim; the UI
uses it to render the image alongside the text. Do not omit any marker.

{markers_str}

"""

    return f"""\
## Coppermind schema
{schema}

## Current wiki index
{index}
{update_note}{images_section}## New source to store: {source_name}{chunk_note}
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
    pages_written: list[str] = []
    link_pattern = re.compile(r"\[\[([^\]]+)\]\]")

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

        # Log [[wiki links]] in the content for traceability.
        links = link_pattern.findall(content)
        if links:
            logger.info(f"[store] Links in '{slug}': {links}")

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
