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


# ------------------------------------------------------------------ #
# Tree navigation — Phase 1                                           #
# ------------------------------------------------------------------ #


def test_flat_mind_has_no_children(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("flat", "tema plano")

    assert mind.children() == []
    assert mind.is_root is True
    assert mind.parent is None


def test_forge_child_creates_structure(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("aventura", "aventura principal")
    child = parent.forge_child("fase-1", "primera fase")

    # Child directories exist
    assert child.path.exists()
    assert child.raw_dir.exists()
    assert child.wiki_dir.exists()
    assert child.config_path.exists()

    # Parent discovers child
    children = parent.children()
    assert len(children) == 1
    assert children[0].name == "fase-1"

    # Child navigates back to parent
    assert child.parent is not None
    assert child.parent.name == "aventura"
    assert child.is_root is False


def test_forge_child_via_forge_parent_kwarg(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("mod", "módulo")
    child = CopperMind.forge("sub", "sub-módulo", parent=parent)

    assert len(parent.children()) == 1
    assert child.parent.name == "mod"


def test_three_level_tree(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    root = CopperMind.forge("root", "raíz")
    child = root.forge_child("child", "hijo")
    grandchild = child.forge_child("grandchild", "nieto")

    # Navigation down
    assert len(root.children()) == 1
    assert len(child.children()) == 1
    assert len(grandchild.children()) == 0

    # descendants() is recursive
    desc = root.descendants()
    assert len(desc) == 2
    assert {d.name for d in desc} == {"child", "grandchild"}

    # Navigation up
    assert grandchild.parent.name == "child"
    assert grandchild.parent.parent.name == "root"
    assert root.is_root is True
    assert child.is_root is False
    assert grandchild.is_root is False


def test_meta_summary_missing_returns_empty(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("meta-test", "tema")

    assert not mind.meta_summary_path.exists()
    assert mind.meta_summary == ""


def test_meta_summary_reads_file_when_present(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    mind = CopperMind.forge("meta-test", "tema")
    mind.meta_summary_path.write_text("Resumen de la mente.")

    assert mind.meta_summary == "Resumen de la mente."


def test_format_tree(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    root = CopperMind.forge("root", "raíz")
    child1 = root.forge_child("hijo-1", "tema 1")
    root.forge_child("hijo-2", "tema 2")
    child1.forge_child("nieto", "tema nieto")
    child1.meta_summary_path.write_text("Resumen del hijo 1.")

    tree = root.format_tree()

    assert "root/" in tree
    assert "hijo-1/" in tree
    assert "hijo-2/" in tree
    assert "nieto/" in tree
    # hijo-1 appears before nieto (parent before children within a branch)
    assert tree.index("hijo-1/") < tree.index("nieto/")
    # meta summary is shown
    assert "Resumen del hijo 1." in tree


def test_forge_child_duplicate_raises(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("padre", "tema")
    parent.forge_child("hijo", "subtema")

    with pytest.raises(FileExistsError):
        parent.forge_child("hijo", "otro subtema")
