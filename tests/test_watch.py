"""Tests for watchdog auto-ingest handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copper.watch import _RawDirHandler, _IGNORED_NAMES, _IGNORED_SUFFIXES


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _make_handler(mind, llm, *, on_result=None, on_error=None):
    return _RawDirHandler(mind, llm, on_result=on_result, on_error=on_error)


def _mock_mind(tmp_path):
    mind = MagicMock()
    mind.raw_dir = tmp_path / "raw"
    mind.raw_dir.mkdir(parents=True)
    return mind


# ------------------------------------------------------------------ #
# _RawDirHandler                                                      #
# ------------------------------------------------------------------ #


class TestRawDirHandler:
    def test_ignores_ds_store(self, tmp_path):
        f = tmp_path / ".DS_Store"
        f.write_bytes(b"garbage")
        handler = _make_handler(MagicMock(), MagicMock())
        assert not handler._should_process(f)

    def test_ignores_tmp_extensions(self, tmp_path):
        for ext in _IGNORED_SUFFIXES:
            f = tmp_path / f"file{ext}"
            f.write_text("x")
            assert not handler._should_process(f) if (
                handler := _make_handler(MagicMock(), MagicMock())
            ) else True

    def test_processes_md_file(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Hello")
        handler = _make_handler(MagicMock(), MagicMock())
        assert handler._should_process(f)

    def test_processes_pdf_file(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4")
        handler = _make_handler(MagicMock(), MagicMock())
        assert handler._should_process(f)

    def test_calls_on_result_on_success(self, tmp_path):
        mind = _mock_mind(tmp_path)
        llm = MagicMock()
        results = []

        f = mind.raw_dir / "note.md"
        f.write_text("# Note\n\nContent.")

        mock_result = MagicMock()
        mock_result.source = "note.md"
        mock_result.pages_written = ["note"]

        with patch("copper.watch.StoreWorkflow") as MockWorkflow:
            MockWorkflow.return_value.run.return_value = mock_result
            with patch("copper.watch._wait_for_stable", return_value=True):
                handler = _make_handler(mind, llm, on_result=lambda p, r: results.append(r))
                handler.process(f)

        assert len(results) == 1
        assert results[0].source == "note.md"

    def test_calls_on_error_when_workflow_raises(self, tmp_path):
        mind = _mock_mind(tmp_path)
        llm = MagicMock()
        errors = []

        f = mind.raw_dir / "broken.md"
        f.write_text("content")

        with patch("copper.watch.StoreWorkflow") as MockWorkflow:
            MockWorkflow.return_value.run.side_effect = RuntimeError("LLM timeout")
            with patch("copper.watch._wait_for_stable", return_value=True):
                handler = _make_handler(
                    mind, llm, on_error=lambda p, e: errors.append(e)
                )
                handler.process(f)

        assert len(errors) == 1
        assert "LLM timeout" in str(errors[0])

    def test_calls_on_error_when_file_does_not_stabilize(self, tmp_path):
        mind = _mock_mind(tmp_path)
        llm = MagicMock()
        errors = []

        f = mind.raw_dir / "big.pdf"
        f.write_bytes(b"%PDF-1.4 stub")

        with patch("copper.watch._wait_for_stable", return_value=False):
            handler = _make_handler(
                mind, llm, on_error=lambda p, e: errors.append(e)
            )
            handler.process(f)

        assert len(errors) == 1
        assert isinstance(errors[0], TimeoutError)

    def test_skips_ignored_file_silently(self, tmp_path):
        mind = _mock_mind(tmp_path)
        llm = MagicMock()
        results = []
        errors = []

        f = mind.raw_dir / ".DS_Store"
        f.write_bytes(b"garbage")

        handler = _make_handler(
            mind, llm,
            on_result=lambda p, r: results.append(r),
            on_error=lambda p, e: errors.append(e),
        )
        handler.process(f)

        assert results == []
        assert errors == []


# ------------------------------------------------------------------ #
# watch_raw_dir — ImportError path                                    #
# ------------------------------------------------------------------ #


class TestWatchRawDir:
    def test_raises_import_error_if_watchdog_missing(self):
        from copper.watch import watch_raw_dir

        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            with pytest.raises(ImportError, match="watchdog"):
                watch_raw_dir(MagicMock(), MagicMock())
