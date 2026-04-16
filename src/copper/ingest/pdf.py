"""PDF ingest plugin — extracts text via pdfplumber."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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

    def to_markdown(self, path: Path) -> str:
        pages = self._extract_pages(path)
        if not pages:
            return f"<!-- PDF '{path.name}' contains no extractable text -->"
        return "\n\n---\n\n".join(f"<!-- Page {i} -->\n\n{text}" for i, text in pages)

    def to_chunks(self, path: Path, max_chars: int, llm: Any = None) -> list[str]:
        """Hybrid chunking: TOC keyword → TOC pattern → LLM → naive split."""
        pages = self._extract_pages(path)
        if not pages:
            return [f"<!-- PDF '{path.name}' contains no extractable text -->"]

        full_text = "\n\n---\n\n".join(f"<!-- Page {i} -->\n\n{text}" for i, text in pages)

        # Strategy A: locate TOC page and split at section titles
        chunks = self._chunks_from_toc(pages, full_text, max_chars)
        if chunks:
            return chunks

        # Strategy B: ask LLM to identify section boundaries
        if llm is not None:
            chunks = self._chunks_from_llm(full_text, max_chars, llm)
            if chunks:
                return chunks

        # Strategy C: naive character-based split
        return naive_split(full_text, max_chars)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_pages(self, path: Path) -> list[tuple[int, str]]:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber is required to ingest PDF files.\n"
                "Install it with: pdm install -G pdf"
            )
        result: list[tuple[int, str]] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text and text.strip():
                    result.append((i, text.strip()))
        return result

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
        return self._split_by_titles(full_text, titles, max_chars)

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
        self, full_text: str, titles: list[str], max_chars: int
    ) -> list[str]:
        """Locate title anchors in full_text and use them as split boundaries."""
        text_lower = full_text.lower()
        anchors: list[int] = []

        for title in titles:
            needle = title.lower()
            # Prefer a match at the start of a line
            pos = text_lower.find("\n" + needle)
            if pos != -1:
                pos += 1  # skip the leading newline
            else:
                pos = text_lower.find(needle)
            if pos != -1:
                anchors.append(pos)

        if len(anchors) < 2:
            return []

        anchors = sorted(set(anchors))
        anchors.append(len(full_text))

        raw_chunks = [
            full_text[anchors[i]: anchors[i + 1]].strip()
            for i in range(len(anchors) - 1)
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

    def _chunks_from_llm(
        self, full_text: str, max_chars: int, llm: Any
    ) -> list[str]:
        """Ask the LLM to identify section titles from the document opening."""
        from copper.llm.base import Message

        sample = full_text[:_LLM_SAMPLE_CHARS]
        messages = [Message(role="user", content=_LLM_SECTION_PROMPT.format(sample=sample))]

        try:
            response = llm.complete(messages)
        except Exception:
            return []

        titles = [
            line[len("SECTION:"):].strip()
            for line in response.text.splitlines()
            if line.upper().startswith("SECTION:")
        ]
        titles = [t for t in titles if len(t) >= 4]

        if not titles:
            return []

        return self._split_by_titles(full_text, titles, max_chars)
