"""PDF ingest plugin — extracts text via pdfplumber."""

from __future__ import annotations

from pathlib import Path

from copper.ingest.base import IngestPlugin


class PDFPlugin(IngestPlugin):
    """Extracts text from PDF files using pdfplumber.

    Requires the optional 'pdf' extra:
        pdm install -G pdf
    """

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def to_markdown(self, path: Path) -> str:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber is required to ingest PDF files.\n"
                "Install it with: pdm install -G pdf"
            )

        pages: list[str] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text and text.strip():
                    pages.append(f"<!-- Página {i} -->\n\n{text.strip()}")

        if not pages:
            return f"<!-- El PDF '{path.name}' no contiene texto extraíble -->"

        return "\n\n---\n\n".join(pages)
