"""Tests for CopperMind core class."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    """Override MINDS_DIR to use a temp directory."""
    import copper.core.coppermind as cm_module
    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


def test_forge_creates_structure(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("test-mind", "inteligencia artificial")

    assert mind.path.exists()
    assert mind.raw_dir.exists()
    assert (mind.raw_dir / "assets").exists()
    assert mind.wiki_dir.exists()
    assert mind.outputs_dir.exists()
    assert mind.meta_dir.exists()
    assert mind.config_path.exists()
    assert mind.schema_path.exists()
    assert mind.index_path.exists()
    assert mind.log_path.exists()


def test_forge_config_values(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("mi-mente", "cosmere lore")

    assert mind.config.name == "mi-mente"
    assert mind.config.topic == "cosmere lore"
    assert mind.config.linked_minds == []


def test_forge_duplicate_raises(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("duplicada", "tema")
    with pytest.raises(FileExistsError):
        CopperMind.forge("duplicada", "otro tema")


def test_get_nonexistent_raises(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    with pytest.raises(FileNotFoundError):
        CopperMind.get("no-existe")


def test_list_all(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("alpha", "tema A")
    CopperMind.forge("beta", "tema B")

    minds = CopperMind.list_all()
    names = [m.name for m in minds]
    assert "alpha" in names
    assert "beta" in names


def test_resolve_many_single(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("solo", "tema")
    minds = CopperMind.resolve_many("solo")
    assert len(minds) == 1
    assert minds[0].name == "solo"


def test_resolve_many_comma_separated(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("uno", "tema 1")
    CopperMind.forge("dos", "tema 2")
    minds = CopperMind.resolve_many("uno,dos")
    assert {m.name for m in minds} == {"uno", "dos"}


def test_resolve_many_all(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    CopperMind.forge("x", "tema x")
    CopperMind.forge("y", "tema y")
    minds = CopperMind.resolve_many("--all")
    assert len(minds) == 2


def test_append_log(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("log-test", "tema")
    mind.append_log("test", "entrada de prueba")
    log_content = mind.log_path.read_text()
    assert "test" in log_content
    assert "entrada de prueba" in log_content


def test_stats(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("stats-test", "mi tema")
    stats = mind.stats()
    assert stats["name"] == "stats-test"
    assert stats["topic"] == "mi tema"
    assert stats["raw_sources"] == 0
    assert stats["wiki_pages"] == 0
