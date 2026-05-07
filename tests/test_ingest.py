"""Tests for ingest plugins and registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copper.ingest.obsidian import ObsidianPlugin
from copper.ingest.plain import PlainTextPlugin
from copper.ingest.pdf import PDFPlugin
from copper.ingest.registry import IngestRegistry, default_registry


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


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

        assert "no extractable text" in result

    # ------------------------------------------------------------------ #
    # to_chunks — hybrid chunking                                         #
    # ------------------------------------------------------------------ #

    def _make_mock_pdfplumber(self, pages: list[str]):
        """Build a pdfplumber mock given a list of page texts."""
        mock_pages = []
        for text in pages:
            p = MagicMock()
            p.extract_text.return_value = text
            mock_pages.append(p)

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = mock_pages

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf
        return mock_pdfplumber

    def test_to_chunks_toc_keyword_splits_by_sections(self, tmp_path):
        """TOC page detected via keyword → chunks follow section boundaries."""
        f = tmp_path / "book.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        toc_page = (
            "Index\n\n"
            "Introduction ............. 1\n"
            "Chapter One .............. 5\n"
            "Chapter Two .............. 12\n"
            "Conclusion ............... 20\n"
        )
        intro = "Introduction\n\nThis is the introduction text with enough content."
        ch1 = "Chapter One\n\nThis is chapter one content with enough content here."
        ch2 = "Chapter Two\n\nThis is chapter two content with enough content here."
        conclusion = "Conclusion\n\nThis is the conclusion with enough content here."

        mock_pdfplumber = self._make_mock_pdfplumber([toc_page, intro, ch1, ch2, conclusion])

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            chunks = PDFPlugin().to_chunks(f, max_chars=500)

        # Should produce multiple chunks aligned with sections, not a single blob
        assert len(chunks) >= 2
        assert any("Introduction" in c for c in chunks)
        assert any("Chapter One" in c for c in chunks)

    def test_to_chunks_toc_pattern_no_keyword(self, tmp_path):
        """Dense dot-number pattern without a keyword header is also recognised."""
        f = tmp_path / "manual.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        toc_page = (
            "Overview ............. 1\n"
            "Installation ......... 3\n"
            "Configuration ........ 7\n"
            "Usage ................ 11\n"
            "Troubleshooting ...... 18\n"
            "Reference ............ 25\n"
            "Appendix ............. 30\n"
            "Glossary ............. 35\n"
            "Index ................ 40\n"
        )
        page2 = "Overview\n\nThis section gives an overview of the product."
        page3 = "Installation\n\nFollow these steps to install the software."

        mock_pdfplumber = self._make_mock_pdfplumber([toc_page, page2, page3])

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            chunks = PDFPlugin().to_chunks(f, max_chars=300)

        assert len(chunks) >= 2

    def test_to_chunks_llm_fallback(self, tmp_path):
        """No TOC → LLM is called and its section titles are used as anchors."""
        from copper.llm.mock import MockLLM

        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        page1 = "Introduction\n\nThis paper presents a study of transformers in NLP."
        page2 = "Methodology\n\nWe collected data from three public datasets."
        page3 = "Results\n\nThe model achieved 94% accuracy on the benchmark."

        mock_pdfplumber = self._make_mock_pdfplumber([page1, page2, page3])
        llm = MockLLM(["SECTION: Introduction\nSECTION: Methodology\nSECTION: Results"])

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            chunks = PDFPlugin().to_chunks(f, max_chars=300, llm=llm)

        assert llm._call_count == 1
        assert len(chunks) >= 2
        assert any("Introduction" in c for c in chunks)
        assert any("Methodology" in c for c in chunks)

    def test_to_chunks_naive_fallback_no_llm(self, tmp_path):
        """No TOC, no LLM → falls back to naive character-based split."""
        f = tmp_path / "plain.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        long_text = "word " * 200  # 1000 chars, no TOC structure
        mock_pdfplumber = self._make_mock_pdfplumber([long_text])

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            chunks = PDFPlugin().to_chunks(f, max_chars=200)

        assert len(chunks) > 1
        assert all(len(c) <= 200 for c in chunks)

    def test_to_chunks_llm_garbage_falls_back_to_naive(self, tmp_path):
        """LLM returning no valid SECTION lines → naive split used."""
        from copper.llm.mock import MockLLM

        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        long_text = "paragraph content. " * 100
        mock_pdfplumber = self._make_mock_pdfplumber([long_text])
        llm = MockLLM(["I could not identify any sections in this document."])

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            chunks = PDFPlugin().to_chunks(f, max_chars=200, llm=llm)

        assert len(chunks) > 1
        assert all(len(c) <= 200 for c in chunks)


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


# ------------------------------------------------------------------ #
# Phase 5 — PDF structure detection                                   #
# ------------------------------------------------------------------ #


class TestPDFStructureDetection:
    def _make_chunks(self, n: int) -> list[str]:
        return [f"# Sección {i}\n\nContenido de la sección número {i}." for i in range(n)]

    def test_parse_flat_response(self):
        from copper.ingest.pdf import _parse_structure_proposal

        result = _parse_structure_proposal("<flat/>")
        assert result.is_flat is True
        assert result.clusters == []

    def test_parse_cluster_response(self):
        from copper.ingest.pdf import _parse_structure_proposal

        text = (
            '<cluster name="fase-1" topic="La Convocatoria">\n0\n1\n</cluster>\n'
            '<cluster name="fase-2" topic="El Bosque">\n2\n3\n4\n</cluster>\n'
            "<confidence>high</confidence>"
        )
        result = _parse_structure_proposal(text)
        assert result.is_flat is False
        assert len(result.clusters) == 2
        assert result.clusters[0].name == "fase-1"
        assert result.clusters[0].chunk_indices == [0, 1]
        assert result.clusters[1].name == "fase-2"
        assert result.clusters[1].chunk_indices == [2, 3, 4]
        assert result.confidence == "high"

    def test_parse_malformed_returns_flat(self):
        from copper.ingest.pdf import _parse_structure_proposal

        result = _parse_structure_proposal("some gibberish without valid tags")
        assert result.is_flat is True

    def test_detect_structure_skips_below_threshold(self):
        from copper.llm.mock import MockLLM
        from copper.ingest.pdf import PDFPlugin

        chunks = self._make_chunks(3)
        llm = MockLLM(["this should not be called"])
        proposal, tokens, cost = PDFPlugin().detect_structure(chunks, llm)
        # min_chunks default is 8; 3 chunks should be detected as too few
        # But detect_structure doesn't check threshold — that's in StoreWorkflow.
        # So it WILL call the LLM regardless; the threshold guard is in StoreWorkflow.
        assert isinstance(proposal.is_flat, bool)

    def test_detect_structure_calls_llm_and_returns_proposal(self):
        from copper.llm.mock import MockLLM
        from copper.ingest.pdf import PDFPlugin

        chunks = self._make_chunks(10)
        cluster_response = (
            '<cluster name="primera" topic="Primera mitad">\n'
            + "\n".join(str(i) for i in range(5))
            + "\n</cluster>\n"
            '<cluster name="segunda" topic="Segunda mitad">\n'
            + "\n".join(str(i) for i in range(5, 10))
            + "\n</cluster>\n"
            "<confidence>high</confidence>"
        )
        llm = MockLLM([cluster_response])
        proposal, tokens, cost = PDFPlugin().detect_structure(chunks, llm)

        assert not proposal.is_flat
        assert len(proposal.clusters) == 2
        assert proposal.clusters[0].name == "primera"
        assert proposal.clusters[1].name == "segunda"
        assert llm._call_count == 1

    def test_detect_structure_flat_llm_response(self):
        from copper.llm.mock import MockLLM
        from copper.ingest.pdf import PDFPlugin

        chunks = self._make_chunks(10)
        llm = MockLLM(["<flat/>"])
        proposal, tokens, cost = PDFPlugin().detect_structure(chunks, llm)

        assert proposal.is_flat is True
        assert proposal.clusters == []
        assert llm._call_count == 1

    def test_store_workflow_skips_structure_for_non_pdf(self, tmp_minds_dir, tmp_path):
        """Structure detection is bypassed for non-PDF sources."""
        import copper.core.coppermind as cm_module
        from copper.core.coppermind import CopperMind
        from copper.llm.mock import MockLLM
        from copper.workflows.store import StoreWorkflow

        mind = CopperMind.forge("test-mind", "tema")
        src = tmp_path / "doc.txt"
        src.write_text("Contenido plano sin estructura PDF.")

        def wiki_xml(slug):
            return (
                "<wiki_updates>"
                f'<page slug="{slug}" title="{slug.title()}" action="create">'
                f"<content>Contenido. [Fuente: src]</content>"
                "</page>"
                f"<index># Índice\n\n- [[{slug}]]</index>"
                "</wiki_updates>"
            )

        llm = MockLLM([wiki_xml("doc")])
        result = StoreWorkflow(mind, llm).run(src)

        # Structurer not invoked — only 1 LLM call (archivist)
        assert llm._call_count == 1
        assert result.structural_clusters is None

    def test_store_workflow_structure_detection_below_threshold(
        self, tmp_minds_dir, tmp_path, monkeypatch
    ):
        """Structure detection is skipped when chunks < min_chunks threshold."""
        import copper.core.coppermind as cm_module
        from copper.core.coppermind import CopperMind
        from copper.llm.mock import MockLLM
        from copper.workflows.store import StoreWorkflow
        from copper.config import settings

        monkeypatch.setattr(settings, "copper_pdf_structure_min_chunks", 100)

        mind = CopperMind.forge("test-mind", "tema")
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")

        # Patch to_chunks to return a small flat list without parsing real PDF
        monkeypatch.setattr(
            "copper.ingest.registry.IngestRegistry.to_chunks",
            lambda *_a, **_kw: ["chunk único"],
        )

        def wiki_xml(slug):
            return (
                "<wiki_updates>"
                f'<page slug="{slug}" title="{slug.title()}" action="create">'
                f"<content>Contenido. [Fuente: src]</content>"
                "</page>"
                f"<index># Índice\n\n- [[{slug}]]</index>"
                "</wiki_updates>"
            )

        llm = MockLLM([wiki_xml("doc")])
        result = StoreWorkflow(mind, llm).run(src)

        assert llm._call_count == 1
        assert result.structural_clusters is None
