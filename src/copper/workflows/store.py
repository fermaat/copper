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
from copper.core.meta import regenerate_meta
from copper.core.wiki import WikiManager
from copper.ingest.registry import default_registry
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt

if TYPE_CHECKING:
    from copper.ingest.image_describer import ImageDescriber
    from copper.ingest.pdf import StructureProposal


# Maximum characters sent to the LLM per chunk. Override via COPPER_STORE_MAX_CHUNK_CHARS.
MAX_CHUNK_CHARS = settings.copper_store_max_chunk_chars

# Number of additional attempts after the first if the LLM returns no valid XML.
# 2 retries: attempt 1 uses an empty-response or malformed-XML hint; attempt 2
# is cheap insurance for residual cases after the relaxed parser handles most
# truncations on the first try.
_MAX_XML_RETRIES = 2

# Visual marker emitted by the PDF ingest. Carries page+image coordinates and a
# short description with optional "(Keywords: …)" tail used by the safety net.
_VISUAL_MARKER_RE = re.compile(r"\[Visual on page \d+, image \d+:[^\]]+\]")
_VISUAL_MARKER_ID_RE = re.compile(r"\[Visual on page \d+, image \d+:")
_VISUAL_KEYWORDS_RE = re.compile(r"\((?:keywords|tags):\s*([^)]+)\)", re.IGNORECASE)
_VISUAL_PREFIX_RE = re.compile(r"^\[Visual on page \d+, image \d+:\s*")

# Words from the marker that carry no semantic signal — they appear in every
# marker by construction and would otherwise bias the scoring towards any page
# that already contains a visual.
_MARKER_STOPWORDS = frozenset({"keywords", "tags", "visual", "image", "page", "pages"})

# ---- XML normalization + relaxed parser ---------------------------------- #


def _normalize_xml(text: str) -> str:
    """Pre-process LLM output before XML parsing.

    Handles the three most common failure modes:
    - Markdown code fences wrapping the XML block.
    - Smart/curly quotes injected by some models.
    - Stray leading/trailing whitespace.
    """
    text = text.strip()
    # Strip a single markdown fence (```xml or ```) at start and end.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    # Replace smart quotes with straight equivalents.
    for smart, straight in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(smart, straight)
    return text.strip()


_PAGE_OPEN_RE = re.compile(r"<page\s+([^>]*)>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_wiki_pages(text: str) -> list[tuple[str, str, str, str, bool]]:
    """Parse <page> elements from normalized LLM output, tolerating:
    - Attributes in any order (slug/title/action).
    - Truncated output: missing </content> or </page> — auto-closed at segment boundary.
    - Pages without <content> tags — segment body recovered.

    Pages with empty body after extraction are skipped with a warning to avoid
    silently overwriting existing wiki pages with nothing.

    Returns a list of (slug, title, action, content, was_auto_closed) tuples.
    ``was_auto_closed`` is True when the page's segment was truncated (missing
    both </content> and </page>, or missing </page> with no content tag at all).
    The caller uses this to refuse destructive overwrites on existing pages.
    """
    results: list[tuple[str, str, str, str, bool]] = []
    opens = list(_PAGE_OPEN_RE.finditer(text))
    for i, m in enumerate(opens):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        slug = attrs.get("slug", "")
        title = attrs.get("title", "")
        action = attrs.get("action", "create")
        if not slug or not title:
            continue

        # Segment: from end of <page ...> to next <page or EOF.
        seg_start = m.end()
        seg_end = opens[i + 1].start() if i + 1 < len(opens) else len(text)
        segment = text[seg_start:seg_end]

        # Extract content between <content> and </content>, auto-closing if truncated.
        was_auto_closed = False
        c_open = segment.find("<content>")
        if c_open == -1:
            # No <content> tag — recover body from the page segment itself.
            # Some models (e.g. gemma4) drop the inner tags and put the body
            # directly inside <page>...</page>. Treat that segment as the body.
            page_close = segment.find("</page>")
            content = segment[: page_close if page_close != -1 else len(segment)]
            if page_close == -1:
                # Neither <content> nor </page> — body bounds are unreliable.
                was_auto_closed = True
            if content.strip():
                if was_auto_closed:
                    logger.warning(f"[store] Page '{slug}' truncated (no <content> or </page>)")
                else:
                    logger.warning(
                        f"[store] Page '{slug}' missing <content> tags — using segment as body"
                    )
        else:
            body_start = c_open + len("<content>")
            c_close = segment.find("</content>", body_start)
            if c_close == -1:
                # Truncated: close at </page> boundary or segment end.
                page_close = segment.find("</page>", body_start)
                content = segment[body_start : page_close if page_close != -1 else len(segment)]
                was_auto_closed = True
                logger.warning(f"[store] Auto-closed truncated page '{slug}'")
            else:
                content = segment[body_start:c_close]

        content = content.strip()
        if not content:
            # Never persist an empty body — would overwrite an existing page with nothing.
            # When no pages are written at all, _apply_wiki_updates falls back to retry.
            logger.warning(f"[store] Skipping page '{slug}': empty content after parse")
            continue

        results.append((slug, title, action, content, was_auto_closed))
    return results


# Loaded lazily via render_prompt() inside the workflow so a missing YAML
# surfaces early with a clear error, rather than at module import time.
_STORE_SYSTEM_PROMPT = "store.archivist"
_STORE_ROUTER_PROMPT = "store.router"

# Max chars of source content sent to the router as a preview.
_ROUTER_PREVIEW_CHARS = 2_000


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

    def _route(self, source_path: Path) -> tuple["CopperMind", str | None, int, float]:
        """Call the router LLM and resolve the destination mind.

        Returns (target_mind, routed_to_name, tokens_used, cost_usd).
        routed_to_name is None when the decision is to keep at parent level.
        """
        children = self.mind.children()
        children_block = "\n".join(
            f"- {c.name}: {c.meta_summary or '(sin resumen)'}" for c in children
        )
        try:
            content_preview = source_path.read_text(errors="replace")[:_ROUTER_PREVIEW_CHARS]
        except Exception:
            content_preview = source_path.name

        user_content = render_prompt(
            _STORE_ROUTER_PROMPT,
            parent_topic=self.mind.config.topic,
            children_list=children_block,
            content_preview=content_preview,
        )
        messages = [
            Message(role="system", content=render_prompt(_STORE_SYSTEM_PROMPT)),
            Message(role="user", content=user_content),
        ]
        logger.info(
            f"[store] Routing '{source_path.name}' across " f"{len(children)} sub-copperminds..."
        )
        response = self.llm.complete(messages)
        target_str, new_child_topic = _parse_router_response(response.text)
        logger.info(f"[store] Router decision: '{target_str}'")

        if target_str == "parent":
            return self.mind, None, response.tokens_used, response.cost_usd

        if target_str.startswith("new_child:"):
            child_name = target_str[len("new_child:") :]
            topic = new_child_topic or f"Sub-topic of {self.mind.config.topic}"
            logger.info(f"[store] Forjando nueva sub-mentecobre '{child_name}' (tema: {topic})...")
            new_child = self.mind.forge_child(child_name, topic)
            return new_child, child_name, response.tokens_used, response.cost_usd

        child_map = {c.name: c for c in children}
        child = child_map.get(target_str)
        if child is None:
            logger.warning(
                f"[store] Router returned unknown child '{target_str}', " "falling back to parent"
            )
            return self.mind, None, response.tokens_used, response.cost_usd

        return child, target_str, response.tokens_used, response.cost_usd

    def _detect_pdf_structure(
        self, path: Path, chunks: list[str]
    ) -> tuple["StructureProposal | None", int, float]:
        """Run structure detection on a PDF source, if eligible.

        Returns (proposal_or_None, tokens_used, cost_usd).
        """
        if path.suffix.lower() != ".pdf":
            return None, 0, 0.0
        if len(chunks) < settings.copper_pdf_structure_min_chunks:
            return None, 0, 0.0

        from copper.ingest.pdf import PDFPlugin

        proposal, tokens, cost = PDFPlugin().detect_structure(chunks, self.llm)
        return proposal, tokens, cost

    def _run_structural_ingest(
        self,
        source_name: str,
        chunks: list[str],
        proposal: "StructureProposal",
        pre_tokens: int,
        pre_cost: float,
    ) -> "StoreResult":
        """Store each cluster of chunks into its own child coppermind."""
        import tempfile

        all_pages: list[str] = []
        total_tokens = pre_tokens
        total_cost = pre_cost
        cluster_names: list[str] = []

        existing_children = {c.name: c for c in self.mind.children()}

        for cluster in proposal.clusters:
            child = existing_children.get(cluster.name) or self.mind.forge_child(
                cluster.name, cluster.topic
            )
            cluster_names.append(cluster.name)

            cluster_text = "\n\n---\n\n".join(
                chunks[i] for i in cluster.chunk_indices if i < len(chunks)
            )

            with tempfile.NamedTemporaryFile(
                suffix=".md",
                prefix=f"copper_cluster_{cluster.name}_",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as tmp:
                tmp.write(cluster_text)
                tmp_path = Path(tmp.name)

            try:
                child_wf = StoreWorkflow(child, self.llm, self.image_describer)
                child_result = child_wf.run(tmp_path, no_route=True)
                all_pages.extend(child_result.pages_written)
                total_tokens += child_result.tokens_used
                total_cost += child_result.cost_usd
                logger.info(
                    f"[store] Cluster '{cluster.name}': "
                    f"{len(child_result.pages_written)} pages written"
                )
            finally:
                tmp_path.unlink(missing_ok=True)

        self.mind.append_log(
            "store",
            f"'{source_name}' estructurado en {len(proposal.clusters)} sub-mentecobres: "
            + ", ".join(cluster_names),
        )
        logger.info(
            f"[store] Structural ingest done: {len(proposal.clusters)} clusters, "
            f"{len(all_pages)} total pages"
        )
        return StoreResult(
            source=source_name,
            pages_written=all_pages,
            tokens_used=total_tokens,
            cost_usd=total_cost,
            structural_clusters=cluster_names,
        )

    def run(
        self,
        source_path: Path,
        no_route: bool = False,
        into: str | None = None,
    ) -> "StoreResult":
        if not source_path.exists():
            raise FileNotFoundError(f"Fuente no encontrada: {source_path}")

        # --- Routing pass (before touching raw/) ---
        router_tokens = 0
        router_cost = 0.0
        target_mind = self.mind
        routed_to: str | None = None

        if into is not None:
            child_map = {c.name: c for c in self.mind.children()}
            child = child_map.get(into)
            if child is None:
                raise ValueError(f"Sub-mentecobre '{into}' no encontrada en '{self.mind.name}'.")
            target_mind = child
            routed_to = child.name
        elif not no_route and self.mind.children():
            target_mind, routed_to, router_tokens, router_cost = self._route(source_path)

        if target_mind is not self.mind:
            child_wf = StoreWorkflow(target_mind, self.llm, self.image_describer)
            child_result = child_wf.run(source_path, no_route=True)
            # Child's _meta was refreshed by its own run(). Refresh parent so its
            # summary reflects the child's new content.
            regenerate_meta(self.mind, self.llm)
            return StoreResult(
                source=child_result.source,
                pages_written=child_result.pages_written,
                tokens_used=child_result.tokens_used + router_tokens,
                cost_usd=child_result.cost_usd + router_cost,
                routed_to=routed_to,
            )

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

        # Phase 5: detect structure in large PDFs and fork to child copperminds.
        if not no_route:
            proposal, struct_tokens, struct_cost = self._detect_pdf_structure(raw_path, chunks)
            if proposal is not None and not proposal.is_flat:
                return self._run_structural_ingest(
                    source_name,
                    chunks,
                    proposal,
                    pre_tokens=router_tokens + struct_tokens,
                    pre_cost=router_cost + struct_cost,
                )

        schema = self.mind.schema()
        all_pages: list[str] = []
        total_tokens = 0
        total_cost = 0.0
        carry_markers: list[str] = []

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
            unplaced = _inject_missing_visual_markers(
                chunk, pages, self.wiki, existing_before, carry_markers
            )

            is_last_ingot = i == total_ingots
            if unplaced and not is_last_ingot:
                # Cap the buffer to avoid pathological growth.
                if len(unplaced) > _CARRY_MARKER_CAP:
                    dropped = unplaced[_CARRY_MARKER_CAP:]
                    unplaced = unplaced[:_CARRY_MARKER_CAP]
                    for m in dropped:
                        label = _marker_short_label(m)
                        logger.warning(f"[store] Carry-over buffer full — dropping marker: {label}")
                carry_markers = unplaced
                logger.info(f"[store] Carry-over: {len(unplaced)} marker(s) deferred to next ingot")
            elif unplaced and is_last_ingot:
                # Last ingot: any remaining unplaced markers are genuinely homeless.
                for marker in unplaced:
                    m_id = _marker_id(marker)
                    label = _marker_short_label(marker)
                    logger.warning(
                        f"[store] Dropping orphan marker {m_id}: {label} "
                        f"(LLM wrote pages: {sorted(pages)}). "
                        "No page scored above the placement-confidence floor — "
                        "the marker's subject was likely merged into another page "
                        "or omitted entirely. Inspect the listed pages and re-run "
                        "polish to surface the gap."
                    )
            else:
                carry_markers = []

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

        # After multi-ingot forging, run polish to consolidate duplicates and fix gaps.
        # PolishWorkflow.run() calls regenerate_meta internally, so no extra call needed.
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
        else:
            regenerate_meta(self.mind, self.llm)

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


# Confidence floor for orphan-marker injection. Below this, the safety net
# drops the marker rather than risk placing it on an unrelated page. 50 is
# the score for "slug name appears verbatim inside the marker" — anything
# weaker is too noisy to act on.
_MIN_PLACEMENT_CONFIDENCE = 50


def _pick_best_slug(
    marker: str,
    bodies: dict[str, str],
    min_confidence: int = _MIN_PLACEMENT_CONFIDENCE,
) -> str | None:
    """Pick the wiki slug most semantically related to a visual marker.

    Returns ``None`` when no slug clears ``min_confidence`` — placing a marker
    on an unrelated page is worse than dropping it, since a wrong image is
    actively misleading while a missing one only fails to render.

    Scoring (higher is better):
    - +100 if any marker keyword appears in the slug name itself.
    - +20 per marker keyword found in the page body.
    - +50 if the slug-as-words appears inside the marker after punctuation
      normalisation (so 'regal-relayform' matches 'Regal: Relayform').
    - +30 if every slug token appears somewhere in the marker (handles word
      reordering, e.g. slug 'regal-relayform' vs marker 'Relayform, Regal').
    - +2 per distinctive description word found in the page body.

    Ties resolve to the first slug in iteration order.
    """
    kws = _marker_keywords(marker)
    desc_words = _marker_description_words(marker)
    marker_low = marker.lower()
    # Normalise punctuation so 'Regal: Relayform' matches slug 'regal-relayform'.
    marker_normalised = re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", marker_low)).strip()
    marker_token_set = set(marker_normalised.split())

    best_slug: str | None = None
    best_score = min_confidence - 1
    for slug, body in bodies.items():
        body_low = body.lower()
        slug_tokens = slug.replace("-", " ").lower().split()
        slug_words = " ".join(slug_tokens)
        score = 0
        if any(k in slug_words for k in kws):
            score += 100
        score += sum(20 for k in kws if k in body_low)
        if slug_words and slug_words in marker_normalised:
            score += 50
        if slug_tokens and all(t in marker_token_set for t in slug_tokens):
            score += 30
        score += sum(2 for w in desc_words if w in body_low)
        if score > best_score:
            best_score = score
            best_slug = slug
    return best_slug


def _marker_short_label(marker: str) -> str:
    """Human-readable label for log messages: entity name + keywords (if any)."""
    body = _VISUAL_PREFIX_RE.sub("", marker)
    body = _VISUAL_KEYWORDS_RE.sub("", body).rstrip("] ").strip()
    name = body[:50].rstrip()
    kws = _marker_keywords(marker)
    suffix = f" — keywords: [{', '.join(kws)}]" if kws else ""
    return f'"{name}"{suffix}'


_CARRY_MARKER_CAP = 20


def _inject_missing_visual_markers(
    chunk: str,
    page_slugs: list[str],
    wiki: WikiManager,
    existing_before: dict[str, str] | None = None,
    carry_markers: list[str] | None = None,
) -> list[str]:
    """Safety net for visual markers after the LLM has written its pages.

    Three responsibilities, in order:
    1. Restore markers that existed in a page BEFORE the LLM update but
       disappeared from its rewritten body.
    2. Inject markers the LLM omitted: carry-over from previous ingots first,
       then this chunk's own markers, scored against the touched pages.
    3. Return a list of markers that could not be placed — the caller carries
       these forward to the next ingot's marker pool.

    Each affected page is written at most once.
    """
    chunk_markers = _extract_visual_markers(chunk)
    all_candidates = list(carry_markers or []) + chunk_markers

    if not page_slugs:
        return all_candidates

    # Read the current (post-LLM) body of every touched page that exists.
    bodies: dict[str, str] = {}
    for slug in page_slugs:
        p = wiki.page(slug)
        if p.exists():
            bodies[slug] = p.body
    if not bodies:
        return all_candidates

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

    # 2. Inject markers: carry-over from previous ingots + this chunk's own.
    #    Unplaced markers are returned for carry-over to the next ingot.
    unplaced: list[str] = []
    if all_candidates:
        present_ids = {
            _marker_id(m) for body in bodies.values() for m in _extract_visual_markers(body)
        }
        for marker in all_candidates:
            m_id = _marker_id(marker)
            if m_id in present_ids:
                continue
            best_slug = _pick_best_slug(marker, bodies)
            if best_slug is None:
                unplaced.append(marker)
                continue
            logger.info(f"[store] Injecting orphan marker {m_id} into '{best_slug}'")
            bodies[best_slug] = bodies[best_slug].rstrip() + "\n\n" + marker + "\n"
            dirty.add(best_slug)
            present_ids.add(m_id)

    # 3. Persist each modified page exactly once.
    for slug in dirty:
        wiki.update_page(slug, bodies[slug])

    return unplaced


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
        images_section = (
            "\n"
            + render_prompt(
                "store.images",
                marker_count=len(visual_markers),
                markers_str=markers_str,
            )
            + "\n"
        )

    return render_prompt(
        "store.user",
        schema=schema,
        index=index,
        update_note=update_note,
        images_section=images_section,
        source_name=source_name,
        chunk_note=chunk_note,
        source_text=source_text,
    )


_EMPTY_RETRY_HINT = (
    "IMPORTANT: Your previous response was empty. The chunk may have been too large.\n"
    "Respond now with a concise <wiki_updates>...</wiki_updates> covering only the most\n"
    "important entities — you can omit minor details.\n"
    "Requirements (all mandatory):\n"
    '- Every <page> tag MUST include all three attributes: slug="...", '
    'title="...", action="create" (or "update" if updating).\n'
    "- Every <content> tag MUST be closed with </content> before </page>.\n"
    "- Every <page> tag MUST be closed with </page>.\n"
    "- The whole block MUST close with </wiki_updates>.\n"
    "- Do NOT wrap the response in markdown code fences (```).\n"
    "- Do NOT add preamble, commentary, or explanation."
)

_MALFORMED_RETRY_HINT = (
    "IMPORTANT: Your previous response did not contain valid, parseable XML.\n"
    "Respond now with ONLY the <wiki_updates>...</wiki_updates> structure.\n"
    "Requirements (all mandatory):\n"
    '- Every <page> tag MUST include all three attributes: slug="...", '
    'title="...", action="create" (or "update" if updating).\n'
    "- Every <content> tag MUST be closed with </content> before </page>.\n"
    "- Every <page> tag MUST be closed with </page>.\n"
    "- The whole block MUST close with </wiki_updates>.\n"
    "- Do NOT wrap the response in markdown code fences (```).\n"
    "- Do NOT add preamble, commentary, or explanation.\n"
    "- Keep each page body concise enough that the full XML fits in your output budget."
)


def _send_with_retry(
    llm: LLMBase, system_prompt: str, user_prompt: str, max_retries: int = _MAX_XML_RETRIES
) -> tuple[str, int, float]:
    """Call the LLM; retry up to max_retries times when no valid <page> XML appears.

    Distinguishes two failure modes for clearer logs and better retry hints:
    - Empty response (0 tokens): uses a "be concise" prompt.
    - Malformed/no XML: uses a strict structure reminder.

    Returns (final_text, total_tokens_across_all_attempts, total_cost).
    Token/cost are accumulated across attempts so the workflow stats stay honest.
    """
    accumulated_text = ""
    accumulated_tokens = 0
    accumulated_cost = 0.0
    last_failure: str = "malformed"

    for attempt in range(max_retries + 1):
        if attempt == 0:
            content = user_prompt
        else:
            hint = _EMPTY_RETRY_HINT if last_failure == "empty" else _MALFORMED_RETRY_HINT
            content = user_prompt + "\n\n---\n" + hint
            logger.info(f"[store] Retry {attempt}/{max_retries} ({last_failure} response)...")

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=content),
        ]
        response = llm.complete(messages)
        accumulated_tokens += response.tokens_used
        accumulated_cost += response.cost_usd
        accumulated_text = response.text
        logger.info(f"[store] LLM responded ({response.tokens_used} tokens, attempt {attempt + 1})")

        normalized = _normalize_xml(response.text)

        if not normalized:
            last_failure = "empty"
            if attempt < max_retries:
                logger.warning(f"[store] Attempt {attempt + 1}: empty response")
            continue

        if re.search(r"<page\s", normalized):
            return accumulated_text, accumulated_tokens, accumulated_cost

        last_failure = "malformed"
        if attempt < max_retries:
            preview = normalized.replace("\n", " ")[:500]
            logger.warning(
                f"[store] Attempt {attempt + 1}: malformed XML. "
                f"Preview: {preview}{'…' if len(normalized) > 500 else ''}"
            )

    return accumulated_text, accumulated_tokens, accumulated_cost


def _apply_wiki_updates(llm_output: str, source_name: str, wiki: WikiManager) -> list[str]:
    """Parse <wiki_updates> XML from LLM output and write pages.

    By the time this is called, ``_send_with_retry`` has already exhausted its
    retries — so a missing XML structure here means we genuinely fall back.
    Uses the relaxed parser: tolerates any attribute order and auto-closes truncated pages.
    """
    pages_written: list[str] = []
    link_pattern = re.compile(r"\[\[([^\]]+)\]\]")

    for slug, title, action, content, was_auto_closed in _parse_wiki_pages(
        _normalize_xml(llm_output)
    ):
        # Destructive-overwrite guard: a truncated body must never replace an
        # existing page. Better to keep the stale-but-complete content and let
        # the next ingest/polish refine it than to wipe real content with a
        # half-written stub. New pages can still accept best-effort partial
        # content (something is better than nothing when there's nothing yet).
        if was_auto_closed and wiki.page(slug).exists():
            logger.warning(
                f"[store] Skipping truncated update for '{slug}': would overwrite "
                "existing content with partial body. Re-run polish to refine."
            )
            continue

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


def _parse_router_response(text: str) -> tuple[str, str]:
    """Parse router output. Returns (target, new_child_topic).

    target is one of: 'parent', a child name, or 'new_child:<name>'.
    new_child_topic is only populated when target starts with 'new_child:'.
    Falls back to 'parent' on malformed output.
    """
    route_match = re.search(r"<route>(.*?)</route>", text, re.DOTALL)
    topic_match = re.search(r"<topic>(.*?)</topic>", text, re.DOTALL)
    target = route_match.group(1).strip() if route_match else "parent"
    topic = topic_match.group(1).strip() if topic_match else ""
    return target, topic


class StoreResult:
    def __init__(
        self,
        source: str,
        pages_written: list[str],
        tokens_used: int,
        cost_usd: float = 0.0,
        routed_to: str | None = None,
        structural_clusters: list[str] | None = None,
    ):
        self.source = source
        self.pages_written = pages_written
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd
        self.routed_to = routed_to
        self.structural_clusters = structural_clusters

    def __repr__(self) -> str:
        routed = f", routed_to={self.routed_to!r}" if self.routed_to else ""
        clustered = f", clusters={self.structural_clusters!r}" if self.structural_clusters else ""
        return (
            f"StoreResult(source={self.source!r}, pages={len(self.pages_written)}, "
            f"tokens={self.tokens_used}, cost=${self.cost_usd:.6f}{routed}{clustered})"
        )
