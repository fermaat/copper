"""Tests for A.1: copper move command."""

from __future__ import annotations

import re

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


@pytest.fixture
def two_flat_minds(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    alpha = CopperMind.forge("alpha", "first mind")
    beta = CopperMind.forge("beta", "second mind")
    alpha.wiki.upsert_page("page-one", "Page One", "body content")
    return alpha, beta


@pytest.fixture
def parent_child_minds(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("padre", "parent mind")
    child = parent.forge_child("hijo", "child mind")
    parent.wiki.upsert_page("migrant", "Migrant Page", "some content")
    return parent, child


# ------------------------------------------------------------------ #
# Happy paths                                                         #
# ------------------------------------------------------------------ #


def test_move_between_flat_minds(two_flat_minds):
    alpha, beta = two_flat_minds
    result = runner.invoke(app, ["move", "page-one", "--from", "alpha", "--to", "beta"])
    out = _strip_ansi(result.output)
    assert result.exit_code == 0, out
    assert "page-one" in out

    from copper.core.coppermind import CopperMind

    alpha2 = CopperMind.get("alpha")
    beta2 = CopperMind.get("beta")
    assert not alpha2.wiki.page("page-one").exists()
    assert beta2.wiki.page("page-one").exists()


def test_move_from_parent_to_child(parent_child_minds):
    parent, child = parent_child_minds
    result = runner.invoke(app, ["move", "migrant", "--from", "padre", "--to", "padre/hijo"])
    out = _strip_ansi(result.output)
    assert result.exit_code == 0, out

    from copper.core.coppermind import CopperMind

    padre = CopperMind.get("padre")
    hijo = next(c for c in padre.children() if c.name == "hijo")
    assert not padre.wiki.page("migrant").exists()
    assert hijo.wiki.page("migrant").exists()


def test_move_writes_both_logs(two_flat_minds):
    alpha, beta = two_flat_minds
    runner.invoke(app, ["move", "page-one", "--from", "alpha", "--to", "beta"])

    from copper.core.coppermind import CopperMind

    alpha2 = CopperMind.get("alpha")
    beta2 = CopperMind.get("beta")
    alpha_log = alpha2.log_path.read_text()
    beta_log = beta2.log_path.read_text()
    assert "page-one" in alpha_log
    assert "move" in alpha_log
    assert "page-one" in beta_log
    assert "move" in beta_log


# ------------------------------------------------------------------ #
# Error cases                                                         #
# ------------------------------------------------------------------ #


def test_move_source_mind_missing(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("beta", "beta")
    result = runner.invoke(app, ["move", "any-slug", "--from", "ghost", "--to", "beta"])
    assert result.exit_code != 0
    assert "ghost" in _strip_ansi(result.output)


def test_move_target_mind_missing(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    alpha = CopperMind.forge("alpha", "alpha")
    alpha.wiki.upsert_page("pg", "Pg", "body")
    result = runner.invoke(app, ["move", "pg", "--from", "alpha", "--to", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in _strip_ansi(result.output)


def test_move_slug_not_in_source(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("alpha", "alpha")
    CopperMind.forge("beta", "beta")
    result = runner.invoke(app, ["move", "no-such-page", "--from", "alpha", "--to", "beta"])
    assert result.exit_code != 0
    assert "no-such-page" in _strip_ansi(result.output)


def test_move_slug_collision_in_target(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    alpha = CopperMind.forge("alpha", "alpha")
    beta = CopperMind.forge("beta", "beta")
    alpha.wiki.upsert_page("clash", "Clash", "from alpha")
    beta.wiki.upsert_page("clash", "Clash", "already in beta")
    result = runner.invoke(app, ["move", "clash", "--from", "alpha", "--to", "beta"])
    assert result.exit_code != 0
    out = _strip_ansi(result.output)
    assert "clash" in out
    # Page must still exist in source (no partial move)
    assert CopperMind.get("alpha").wiki.page("clash").exists()
