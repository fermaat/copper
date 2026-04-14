"""Tests for ingest plugins and registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copper.ingest.obsidian import ObsidianPlugin
from copper.ingest.plain import PlainTextPlugin
from copper.ingest.pdf import PDFPlugin
from copper.ingest.registry import IngestRegistry, default_registry


# ------------------------------------------------------------------ #
# PlainTextPlugin                                                     #
# ------------------------------------------------------------------ #


class TestPlainTextPlugin:
    def test_handles_md(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Hello\n\nWorld.")
        assert PlainTextPlugin().can_handle(f)

    def test_handles_txt(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hello")
        assert PlainTextPlugin().can_handle(f)

    def test_handles_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        assert PlainTextPlugin().can_handle(f)

    def test_rejects_binary(self, tmp_path):
        f = tmp_path / "image.bin"
        f.write_bytes(bytes(range(256)))
        assert not PlainTextPlugin().can_handle(f)

    def test_to_markdown_returns_content(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("Hello, world!")
        assert PlainTextPlugin().to_markdown(f) == "Hello, world!"


# ------------------------------------------------------------------ #
# ObsidianPlugin                                                      #
# ------------------------------------------------------------------ #


class TestObsidianPlugin:
    def test_handles_md_files(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Hello")
        assert ObsidianPlugin().can_handle(f)

    def test_does_not_handle_txt(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hello")
        assert not ObsidianPlugin().can_handle(f)

    def test_normalizes_plain_wikilink(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("See [[Some Page]] for details.")
        result = ObsidianPlugin().to_markdown(f)
        assert "[[" not in result
        assert "Some Page" in result

    def test_normalizes_aliased_wikilink(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("See [[Some Page|this article]] for details.")
        result = ObsidianPlugin().to_markdown(f)
        assert "[[" not in result
        assert "this article" in result
        assert "Some Page" not in result

    def test_strips_image_embeds(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("Here is a diagram: ![[diagram.png]] and more text.")
        result = ObsidianPlugin().to_markdown(f)
        assert "![[" not in result
        assert "diagram.png" not in result
        assert "more text" in result

    def test_leaves_regular_markdown_intact(self, tmp_path):
        f = tmp_path / "note.md"
        content = "# Heading\n\nA paragraph with **bold** and [link](http://example.com)."
        f.write_text(content)
        result = ObsidianPlugin().to_markdown(f)
        assert result == content

    def test_multiple_wikilinks_in_one_file(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("[[Alpha]] and [[Beta|the beta page]] are related.")
        result = ObsidianPlugin().to_markdown(f)
        assert "Alpha" in result
        assert "the beta page" in result
        assert "[[" not in result


# ------------------------------------------------------------------ #
# PDFPlugin                                                           #
# ------------------------------------------------------------------ #


class TestPDFPlugin:
    def test_handles_pdf(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        assert PDFPlugin().can_handle(f)

    def test_does_not_handle_txt(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hello")
        assert not PDFPlugin().can_handle(f)

    def test_raises_if_pdfplumber_missing(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        with patch.dict("sys.modules", {"pdfplumber": None}):
            with pytest.raises(ImportError, match="pdfplumber"):
                PDFPlugin().to_markdown(f)

    def test_extracts_text_via_pdfplumber(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        # Mock pdfplumber so no real PDF parsing is needed
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Abstract\n\nThis paper studies things."
        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = PDFPlugin().to_markdown(f)

        assert "Abstract" in result
        assert "This paper studies things." in result

    def test_empty_pdf_returns_placeholder(self, tmp_path):
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = PDFPlugin().to_markdown(f)

        assert "no contiene texto extraíble" in result


# ------------------------------------------------------------------ #
# IngestRegistry                                                      #
# ------------------------------------------------------------------ #


class TestIngestRegistry:
    def test_default_registry_picks_pdf_for_pdf(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        registry = default_registry()
        # PDFPlugin.can_handle should be True
        assert registry._plugins[0].can_handle(f)

    def test_default_registry_picks_obsidian_for_md(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Hello [[World]]")
        registry = default_registry()
        result = registry.to_markdown(f)
        assert "[[" not in result
        assert "World" in result

    def test_default_registry_picks_plain_for_txt(self, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("plain text content")
        result = default_registry().to_markdown(f)
        assert result == "plain text content"

    def test_registry_raises_for_unknown_binary(self, tmp_path):
        f = tmp_path / "photo.xyz"
        f.write_bytes(bytes(range(256)))  # non-UTF-8
        with pytest.raises(ValueError, match="No ingest plugin"):
            default_registry().to_markdown(f)
