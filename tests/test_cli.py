"""CLI tests — Phase 6 ergonomics + regression for existing commands."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from copper.cli import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


def _stub_store_deps(monkeypatch, responses: list[str] | None = None):
    """Patch LLM getters and StoreWorkflow.run to avoid real LLM calls."""
    from copper.llm.mock import MockLLM
    from copper.workflows.store import StoreResult

    llm = MockLLM(responses or [])
    monkeypatch.setattr("copper.api.deps.get_store_llm", lambda mind: llm)
    monkeypatch.setattr("copper.api.deps.get_ingest_describer", lambda mind: None)

    captured: dict = {}

    def fake_run(self, source_path, no_route=False, into=None, auto_polish=True):
        captured["no_route"] = no_route
        captured["into"] = into
        captured["auto_polish"] = auto_polish
        captured["source"] = source_path
        return StoreResult(source=source_path.name, pages_written=["p1"], tokens_used=0)

    monkeypatch.setattr("copper.workflows.store.StoreWorkflow.run", fake_run)
    return captured


def _stub_polish_deps(monkeypatch):
    """Patch LLM getter and PolishWorkflow.run to avoid real LLM calls."""
    from copper.llm.mock import MockLLM
    from copper.workflows.polish import PolishResult
    from pathlib import Path

    monkeypatch.setattr("copper.api.deps.get_store_llm", lambda mind: MockLLM([]))

    captured: dict = {}

    def fake_run(self, max_depth=None):
        captured["max_depth"] = max_depth
        return PolishResult(
            mind_name=self.mind.name,
            report_path=Path("/tmp/lint.md"),
            report_text="ok",
            structural_issues=[],
            tokens_used=0,
            cost_usd=0.0,
        )

    monkeypatch.setattr("copper.workflows.polish.PolishWorkflow.run", fake_run)
    return captured


# ------------------------------------------------------------------ #
# forge                                                               #
# ------------------------------------------------------------------ #


def test_forge_top_level(tmp_minds_dir):
    result = runner.invoke(app, ["forge", "mi-mente", "--topic", "tema"])
    assert result.exit_code == 0
    from copper.core.coppermind import CopperMind

    assert CopperMind.get("mi-mente").config.topic == "tema"


def test_forge_child_slash_syntax(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("padre", "tema padre")
    result = runner.invoke(app, ["forge", "padre/hijo", "--topic", "tema hijo"])

    assert result.exit_code == 0, result.output
    parent = CopperMind.get("padre")
    children = parent.children()
    assert len(children) == 1
    assert children[0].name == "hijo"
    assert children[0].config.topic == "tema hijo"


def test_forge_child_parent_not_found(tmp_minds_dir):
    result = runner.invoke(app, ["forge", "noexiste/hijo", "--topic", "tema"])
    assert result.exit_code == 1
    assert "noexiste" in result.output


def test_forge_duplicate_child_fails(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("padre", "tema")
    runner.invoke(app, ["forge", "padre/hijo", "--topic", "tema"])
    result = runner.invoke(app, ["forge", "padre/hijo", "--topic", "otro tema"])
    assert result.exit_code == 1


# ------------------------------------------------------------------ #
# store                                                               #
# ------------------------------------------------------------------ #


def test_store_passes_no_route_flag(tmp_minds_dir, tmp_path, monkeypatch):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    src = tmp_path / "doc.txt"
    src.write_text("contenido")

    captured = _stub_store_deps(monkeypatch)
    result = runner.invoke(app, ["store", "mente", str(src), "--no-route"])

    assert result.exit_code == 0, result.output
    assert captured["no_route"] is True
    assert captured["into"] is None


def test_store_passes_into_flag(tmp_minds_dir, tmp_path, monkeypatch):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    src = tmp_path / "doc.txt"
    src.write_text("contenido")

    captured = _stub_store_deps(monkeypatch)
    result = runner.invoke(app, ["store", "mente", str(src), "--into", "fase-1"])

    assert result.exit_code == 0, result.output
    assert captured["into"] == "fase-1"


def test_store_flat_implies_no_route(tmp_minds_dir, tmp_path, monkeypatch):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    src = tmp_path / "doc.txt"
    src.write_text("contenido")

    captured = _stub_store_deps(monkeypatch)
    result = runner.invoke(app, ["store", "mente", str(src), "--flat"])

    assert result.exit_code == 0, result.output
    assert captured["no_route"] is True


def test_store_default_flags_unchanged(tmp_minds_dir, tmp_path, monkeypatch):
    """Existing store invocations pass no_route=False and into=None."""
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    src = tmp_path / "doc.txt"
    src.write_text("contenido")

    captured = _stub_store_deps(monkeypatch)
    result = runner.invoke(app, ["store", "mente", str(src)])

    assert result.exit_code == 0, result.output
    assert captured["no_route"] is False
    assert captured["into"] is None
    assert captured["auto_polish"] is True


def test_store_no_polish_flag(tmp_minds_dir, tmp_path, monkeypatch):
    """--no-polish passes auto_polish=False through to the workflow."""
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    src = tmp_path / "doc.txt"
    src.write_text("contenido")

    captured = _stub_store_deps(monkeypatch)
    result = runner.invoke(app, ["store", "mente", str(src), "--no-polish"])

    assert result.exit_code == 0, result.output
    assert captured["auto_polish"] is False


# ------------------------------------------------------------------ #
# polish                                                              #
# ------------------------------------------------------------------ #


def test_polish_passes_depth_flag(tmp_minds_dir, monkeypatch):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    captured = _stub_polish_deps(monkeypatch)
    result = runner.invoke(app, ["polish", "mente", "--depth", "0"])

    assert result.exit_code == 0, result.output
    assert captured["max_depth"] == 0


def test_polish_default_depth_is_none(tmp_minds_dir, monkeypatch):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("mente", "tema")
    captured = _stub_polish_deps(monkeypatch)
    result = runner.invoke(app, ["polish", "mente"])

    assert result.exit_code == 0, result.output
    assert captured["max_depth"] is None


# ------------------------------------------------------------------ #
# list                                                                #
# ------------------------------------------------------------------ #


def test_list_shows_tree(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("aventura", "Adventure module")
    parent.forge_child("fase-1", "La Convocatoria")
    parent.forge_child("fase-2", "El Bosque")

    result = runner.invoke(app, ["list"])
    out = _strip_ansi(result.output)

    assert result.exit_code == 0
    assert "aventura/" in out
    assert "fase-1/" in out
    assert "fase-2/" in out
    # children appear indented after parent
    assert out.index("aventura/") < out.index("fase-1/")


def test_list_empty(tmp_minds_dir):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "forge" in result.output


def test_list_flat_mind(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("sola", "sin hijos")
    result = runner.invoke(app, ["list"])
    out = _strip_ansi(result.output)

    assert result.exit_code == 0
    assert "sola/" in out
