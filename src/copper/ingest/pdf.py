"""PDF ingest plugin — extracts text via pdfplumber."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core_utils.logger import logger

from copper.config import settings
from copper.ingest.base import IngestPlugin, naive_split

# Matches common TOC header labels (case-insensitive, whole line)
_TOC_HEADER = re.compile(
    r"^\s*(table\s+of\s+contents|contents|index|índice|contenido|tabla\s+de\s+contenidos?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Matches a TOC entry line: "Some Title ......... 42" or "1. Chapter .... 15"
_TOC_ENTRY = re.compile(r"^(.{4,80}?)\s*\.{2,}\s*(\d{1,4})\s*$", re.MULTILINE)

# Leading numbering stripped from section titles ("1.", "Chapter 1", etc.)
_LEADING_NUMBER = re.compile(r"^(\d+\.?\s+|chapter\s+\d+\s*)", re.IGNORECASE)

# Minimum TOC entries required to trust a page as a TOC
_MIN_TOC_ENTRIES = 4

# Pages scanned from the start looking for a TOC
_TOC_SCAN_PAGES = 15

# Characters sent to the LLM for section boundary detection
_LLM_SAMPLE_CHARS = 4_000

# Heuristic filter for image extraction — skip small/decorative images.
# Source of truth in Settings (override via env vars).
_MIN_IMAGE_WIDTH = settings.copper_pdf_min_image_width
_MIN_IMAGE_HEIGHT = settings.copper_pdf_min_image_height
_MIN_IMAGE_AREA = settings.copper_pdf_min_image_area

_LLM_SECTION_PROMPT = """\
The following is the beginning of a document. Identify its main chapters or sections.
For each section write its exact title as it appears in the document — one per line,
prefixed with "SECTION: ". Only list top-level sections (chapters), not subsections.

Document excerpt:
{sample}
"""


class PDFPlugin(IngestPlugin):
    """Extracts text from PDF files using pdfplumber.

    Requires the optional 'pdf' extra:
        pdm install -G pdf
    """

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def to_markdown(self, path: Path, image_describer: Any = None) -> str:
        pages = self._extract_pages(path, image_describer=image_describer)
        if not pages:
            return f"<!-- PDF '{path.name}' contains no extractable text -->"
        return "\n\n---\n\n".join(f"<!-- Page {i} -->\n\n{text}" for i, text in pages)

    def to_chunks(
        self, path: Path, max_chars: int, llm: Any = None, image_describer: Any = None
    ) -> list[str]:
        """Hybrid chunking: TOC keyword → TOC pattern → LLM → naive split."""
        pages = self._extract_pages(path, image_describer=image_describer)
        if not pages:
            return [f"<!-- PDF '{path.name}' contains no extractable text -->"]

        full_text = "\n\n---\n\n".join(f"<!-- Page {i} -->\n\n{text}" for i, text in pages)
        logger.info(f"[pdf] Full text: {len(full_text):,} chars across {len(pages)} pages")

        # Strategy A: locate TOC page and split at section titles
        logger.info("[pdf] Strategy A: scanning for TOC...")
        chunks = self._chunks_from_toc(pages, full_text, max_chars)
        if chunks:
            logger.info(f"[pdf] TOC split → {len(chunks)} chunks")
            return chunks

        # Strategy B: ask LLM to identify section boundaries
        if llm is not None:
            logger.info("[pdf] Strategy B: asking LLM for section boundaries...")
            chunks = self._chunks_from_llm(full_text, max_chars, llm)
            if chunks:
                logger.info(f"[pdf] LLM split → {len(chunks)} chunks")
                return chunks

        # Strategy C: naive character-based split
        logger.info("[pdf] Strategy C: naive character split")
        chunks = naive_split(full_text, max_chars)
        logger.info(f"[pdf] Naive split → {len(chunks)} chunks")
        return chunks

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_pages(self, path: Path, image_describer: Any = None) -> list[tuple[int, str]]:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber is required to ingest PDF files.\n"
                "Install it with: pdm install -G pdf"
            )
        import time

        result: list[tuple[int, str]] = []
        total_images_described = 0
        with pdfplumber.open(path) as pdf:
            total = len(pdf.pages)
            logger.info(
                f"[pdf] Opening '{path.name}' — {total} pages"
                + (" (multimodal enabled)" if image_describer else "")
            )
            for i, page in enumerate(pdf.pages, 1):
                page_start = time.monotonic()
                # layout=True preserves horizontal positioning so multi-column
                # pages (prose + stat blocks, etc.) don't get interleaved.
                text = (page.extract_text(layout=True) or "").strip()
                tables_md = self._extract_tables_as_markdown(page)
                images_md = ""
                if image_describer is not None:
                    images_md, img_stats = self._extract_images_as_markdown(
                        page, text, image_describer
                    )
                    total_images_described += img_stats["described"]
                    elapsed = time.monotonic() - page_start
                    if img_stats["raw"] > 0:
                        logger.info(
                            f"[pdf] Page {i}/{total}: "
                            f"{len(text):,} chars, "
                            f"{img_stats['raw']} images "
                            f"(filtered={img_stats['filtered']}, "
                            f"described={img_stats['described']}, "
                            f"decorative={img_stats['decorative']}, "
                            f"failed={img_stats['failed']}) "
                            f"in {elapsed:.1f}s"
                        )

                parts = [p for p in (text, tables_md, images_md) if p]
                combined = "\n\n".join(parts)
                if combined:
                    result.append((i, combined))

                # Periodic summary every 50 pages (and at the end)
                if i % 50 == 0 or i == total:
                    extra = (
                        f", {total_images_described} images described" if image_describer else ""
                    )
                    logger.info(
                        f"[pdf] Extracted {i}/{total} pages ({len(result)} with text{extra})..."
                    )
        return result

    @staticmethod
    def _extract_images_as_markdown(
        page, context_text: str, describer: Any
    ) -> tuple[str, dict[str, int]]:
        """Extract images that pass the heuristic filter and describe them.

        Returns (markdown_block, stats) where stats is a dict with keys:
        raw, filtered, described, decorative, failed.
        """
        images = getattr(page, "images", None) or []
        stats = {"raw": len(images), "filtered": 0, "described": 0, "decorative": 0, "failed": 0}
        if not images:
            return "", stats

        import io

        descriptions: list[str] = []

        for idx, img in enumerate(images):
            width = float(img.get("width") or 0)
            height = float(img.get("height") or 0)
            if width < _MIN_IMAGE_WIDTH or height < _MIN_IMAGE_HEIGHT:
                stats["filtered"] += 1
                continue
            if width * height < _MIN_IMAGE_AREA:
                stats["filtered"] += 1
                continue

            try:
                # Clamp bbox to page bounds — pdfplumber often reports image
                # boxes slightly outside the page due to float rounding.
                px0, py0, px1, py1 = page.bbox
                bbox = (
                    max(float(img["x0"]), px0),
                    max(float(img["top"]), py0),
                    min(float(img["x1"]), px1),
                    min(float(img["bottom"]), py1),
                )
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    stats["filtered"] += 1
                    continue  # degenerate after clamping
                crop = page.within_bbox(bbox)
                pil_img = crop.to_image(resolution=150).original
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                image_bytes = buf.getvalue()
            except Exception as exc:
                logger.warning(f"[pdf] Could not crop image on page {page.page_number}: {exc}")
                stats["failed"] += 1
                continue

            desc = describer.describe(image_bytes, context_hint=context_text)
            if desc is None:
                stats["failed"] += 1
            elif desc == "":
                stats["decorative"] += 1
            else:
                descriptions.append(f"![{desc}](page-{page.page_number}-img-{idx})")
                stats["described"] += 1

        return ("\n\n".join(descriptions), stats)

    @staticmethod
    def _extract_tables_as_markdown(page) -> str:
        """Extract tables from a PDF page and render them as markdown."""
        try:
            tables = page.extract_tables()
        except Exception:
            return ""
        if not tables:
            return ""

        parts: list[str] = []
        for table in tables:
            rows = [r for r in table if r and any(cell for cell in r)]
            if len(rows) < 2:
                continue
            # First row as header
            header = [str(cell or "").strip() for cell in rows[0]]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")
            for row in rows[1:]:
                cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
                # Pad or trim to match header column count
                while len(cells) < len(header):
                    cells.append("")
                parts.append("| " + " | ".join(cells[: len(header)]) + " |")
            parts.append("")  # blank line between tables
        return "\n".join(parts)

    def _chunks_from_toc(
        self,
        pages: list[tuple[int, str]],
        full_text: str,
        max_chars: int,
    ) -> list[str]:
        toc_text = self._find_toc_page(pages)
        if toc_text is None:
            return []
        titles = self._parse_toc_titles(toc_text)
        if not titles:
            return []
        # Skip past the TOC page when searching for anchors — otherwise every
        # title is found inside the TOC itself, producing dozens of tiny chunks.
        toc_anchor = full_text.lower().find(toc_text[:80].lower())
        search_offset = toc_anchor + len(toc_text) if toc_anchor != -1 else 0
        logger.info(
            f"[pdf] TOC found: {len(titles)} titles, searching from offset {search_offset:,}"
        )
        return self._split_by_titles(full_text, titles, max_chars, search_offset=search_offset)

    def _find_toc_page(self, pages: list[tuple[int, str]]) -> str | None:
        """Return the text of the first TOC-like page found in the opening pages."""
        for _, text in pages[:_TOC_SCAN_PAGES]:
            entry_count = len(_TOC_ENTRY.findall(text))
            # A1: explicit TOC header keyword + at least a few entries
            if _TOC_HEADER.search(text) and entry_count >= _MIN_TOC_ENTRIES:
                return text
            # A2: no keyword but very dense dot-number pattern
            if entry_count >= _MIN_TOC_ENTRIES * 2:
                return text
        return None

    def _parse_toc_titles(self, toc_text: str) -> list[str]:
        """Extract and normalise section titles from a TOC page."""
        titles: list[str] = []
        for m in _TOC_ENTRY.finditer(toc_text):
            title = m.group(1).strip()
            title = _LEADING_NUMBER.sub("", title).strip()
            if len(title) >= 4:
                titles.append(title)
        return titles

    def _split_by_titles(
        self, full_text: str, titles: list[str], max_chars: int, search_offset: int = 0
    ) -> list[str]:
        """Locate title anchors in full_text and use them as split boundaries."""
        text_lower = full_text.lower()
        search_lower = text_lower[search_offset:]
        anchors: list[int] = []

        for title in titles:
            needle = title.lower()
            # Prefer a match at the start of a line
            pos = search_lower.find("\n" + needle)
            if pos != -1:
                pos += 1  # skip the leading newline
            else:
                pos = search_lower.find(needle)
            if pos != -1:
                anchors.append(search_offset + pos)

        if len(anchors) < 2:
            return []

        anchors = sorted(set(anchors))
        anchors.append(len(full_text))

        raw_chunks = [
            full_text[anchors[i] : anchors[i + 1]].strip() for i in range(len(anchors) - 1)
        ]

        result: list[str] = []
        for chunk in raw_chunks:
            if not chunk:
                continue
            if len(chunk) <= max_chars:
                result.append(chunk)
            else:
                result.extend(naive_split(chunk, max_chars))
        return result

    def _chunks_from_llm(self, full_text: str, max_chars: int, llm: Any) -> list[str]:
        """Ask the LLM to identify section titles from the document opening."""
        from copper.llm.base import Message

        sample = full_text[:_LLM_SAMPLE_CHARS]
        messages = [Message(role="user", content=_LLM_SECTION_PROMPT.format(sample=sample))]

        try:
            response = llm.complete(messages)
        except Exception:
            return []

        titles = [
            line[len("SECTION:") :].strip()
            for line in response.text.splitlines()
            if line.upper().startswith("SECTION:")
        ]
        titles = [t for t in titles if len(t) >= 4]

        if not titles:
            return []

        return self._split_by_titles(full_text, titles, max_chars)
