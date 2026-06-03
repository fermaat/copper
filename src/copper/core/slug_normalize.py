"""
Structural slug normalization utilities.

Phase A1: pure structural detection (find_slug_clusters, find_self_links).
Phase A2: LLM-assisted confirmation of merges (propose_merges) — no mutation.
"""

from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from copper.core.wiki import WikiManager
    from copper.llm.base import LLMBase


@dataclass
class SlugCluster:
    """A group of slugs that look like near-duplicates of each other."""

    slugs: list[str]
    reason: str  # e.g. "string-similarity 0.91"


@dataclass
class SelfLink:
    """A page that links to itself via [[slug]]."""

    slug: str


def find_slug_clusters(slugs: list[str], threshold: float) -> list[SlugCluster]:
    """Group slugs whose pairwise string similarity >= threshold.

    Uses difflib.SequenceMatcher ratio on slug strings. Builds clusters by
    transitive union-find so that a—b—c forms one cluster if each pair is
    above the threshold. Singletons are dropped. Output is deterministic:
    inputs are sorted before processing and each cluster's slug list is sorted.
    """
    sorted_slugs = sorted(slugs)
    n = len(sorted_slugs)

    # Union-find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            ratio = difflib.SequenceMatcher(None, sorted_slugs[i], sorted_slugs[j]).ratio()
            if ratio >= threshold:
                union(i, j)

    # Collect groups
    groups: dict[int, list[str]] = {}
    for i, slug in enumerate(sorted_slugs):
        root = find(i)
        groups.setdefault(root, []).append(slug)

    clusters: list[SlugCluster] = []
    for group in sorted(groups.values(), key=lambda g: g[0]):
        if len(group) < 2:
            continue
        # Compute the max pairwise similarity for the reason string.
        max_ratio = 0.0
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                r = difflib.SequenceMatcher(None, group[i], group[j]).ratio()
                if r > max_ratio:
                    max_ratio = r
        clusters.append(
            SlugCluster(
                slugs=sorted(group),
                reason=f"string-similarity {max_ratio:.2f}",
            )
        )
    return clusters


def find_self_links(pages: list) -> list[SelfLink]:
    """Return pages whose body contains a [[<own-slug>]] wikilink.

    Uses a bracket-exact regex so [[masting]] is detected but
    [[masting-ritual]] is not flagged as a self-link in 'masting'.
    """
    result: list[SelfLink] = []
    for page in pages:
        pattern = rf"\[\[{re.escape(page.name)}\]\]"
        if re.search(pattern, page.body):
            result.append(SelfLink(slug=page.name))
    return result


# ── FASE A2 — LLM-assisted merge confirmation ─────────────────────────────── #


@dataclass
class MergeProposal:
    """A confirmed merge: absorb all duplicates into canonical."""

    canonical: str
    duplicates: list[str]
    reason: str


def _build_clusters_context(clusters: list[SlugCluster], pages: list) -> str:
    """Render cluster candidates with title + first body lines for the LLM."""
    page_map = {p.name: p for p in pages}
    lines: list[str] = []
    for cluster in clusters:
        lines.append(f"Cluster: {cluster.slugs}")
        for slug in cluster.slugs:
            page = page_map.get(slug)
            if page is None:
                lines.append(f"  [{slug}] (page not found)")
                continue
            title = page.frontmatter.get("title", slug) if page.frontmatter else slug
            preview = "\n".join(page.body.strip().splitlines()[:3])
            lines.append(f"  [{slug}] {title}\n    {preview}")
        lines.append("")
    return "\n".join(lines).strip()


def _parse_merge_proposals(text: str, valid_slugs: set[str]) -> list[MergeProposal]:
    """Parse <merge> elements from LLM output, returning only valid proposals.

    Tolerates markdown fences, smart quotes, and unparseable output.
    On any parse error, logs a warning and returns [].
    Filters out proposals whose canonical or duplicates are not in valid_slugs.
    """
    # Re-use the same normalization as store.py
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    for smart, straight in ((""", '"'), (""", '"'), ("'", "'"), ("'", "'")):
        text = text.replace(smart, straight)
    text = text.strip()

    if not text:
        return []

    # Wrap in a root element so ET can parse multiple <merge> siblings.
    try:
        root = ET.fromstring(f"<root>{text}</root>")
    except ET.ParseError:
        from core_utils.logger import logger

        logger.warning("[polish] No se pudo parsear la respuesta XML de merge — se ignora")
        return []

    proposals: list[MergeProposal] = []
    for merge_el in root.findall("merge"):
        canonical = (merge_el.get("canonical") or "").strip()
        if not canonical or canonical not in valid_slugs:
            from core_utils.logger import logger

            logger.warning(
                f"[polish] Propuesta ignorada: canonical '{canonical}' no existe en el wiki"
            )
            continue

        duplicates: list[str] = []
        for dup_el in merge_el.findall("duplicate"):
            dup = (dup_el.text or "").strip()
            if dup and dup in valid_slugs and dup != canonical:
                duplicates.append(dup)
            else:
                from core_utils.logger import logger

                logger.warning(
                    f"[polish] Duplicado ignorado: '{dup}' no existe o coincide con canonical"
                )

        reason_el = merge_el.find("reason")
        reason = (reason_el.text or "").strip() if reason_el is not None else ""

        if duplicates:
            proposals.append(
                MergeProposal(canonical=canonical, duplicates=duplicates, reason=reason)
            )

    return proposals


def propose_merges(wiki: "WikiManager", llm: "LLMBase", threshold: float) -> list[MergeProposal]:
    """Structural shortlist → LLM confirmation → MergeProposal list.

    Steps:
    1. Run find_slug_clusters to get structurally similar slug groups.
    2. Build a prompt context with titles + first lines for each candidate.
    3. Call the LLM with the 'polish.merge' prompt.
    4. Parse XML response with _parse_merge_proposals.
    5. Discard proposals referencing slugs outside the structural shortlist.

    Never raises — on any LLM or parse failure, logs a warning and returns [].
    """
    from copper.llm.base import Message
    from copper.prompts import render_prompt

    pages = wiki.all_pages()
    slugs = [p.name for p in pages]
    clusters = find_slug_clusters(slugs, threshold)
    if not clusters:
        return []

    # Only slugs that appear in at least one cluster are valid merge targets.
    shortlisted: set[str] = set()
    for cluster in clusters:
        shortlisted.update(cluster.slugs)

    clusters_context = _build_clusters_context(clusters, pages)

    try:
        messages = [
            Message(
                role="user",
                content=render_prompt("polish.merge", clusters_context=clusters_context),
            ),
        ]
        response = llm.complete(messages)
    except Exception:
        from core_utils.logger import logger

        logger.warning("[polish] Error al llamar al LLM para propuestas de merge — se omite")
        return []

    proposals = _parse_merge_proposals(response.text, valid_slugs=shortlisted)

    # Extra safety: drop proposals whose slugs weren't in the shortlist.
    safe: list[MergeProposal] = []
    for proposal in proposals:
        if proposal.canonical not in shortlisted:
            continue
        valid_dups = [d for d in proposal.duplicates if d in shortlisted]
        if valid_dups:
            safe.append(
                MergeProposal(
                    canonical=proposal.canonical, duplicates=valid_dups, reason=proposal.reason
                )
            )
    return safe
