"""Tests for WikiManager."""

import pytest
from pathlib import Path


@pytest.fixture
def wiki_dir(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# Índice\n")
    (wiki / "log.md").write_text("# Log\n")
    return wiki


def test_create_page(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    page = wm.create_page("mi-tema", "Mi Tema", "Contenido de prueba. [Fuente: test.md]")

    assert page.exists()
    assert "Mi Tema" in page.frontmatter.get("title", "")
    assert "Contenido de prueba" in page.body


def test_update_page(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("pagina", "Página", "Contenido original.")
    wm.update_page("pagina", "Contenido actualizado.")

    page = wm.page("pagina")
    assert "Contenido actualizado" in page.body


def test_upsert_creates_if_missing(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.upsert_page("nueva", "Nueva", "Primer contenido.")
    assert wm.page("nueva").exists()


def test_upsert_updates_if_exists(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("existente", "Existente", "Original.")
    wm.upsert_page("existente", "Existente", "Actualizado.")
    assert "Actualizado" in wm.page("existente").body


def test_slug_normalisation(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    page = wm.create_page("Mi Tema Con Espacios", "Título", "body")
    assert page.path.name == "mi-tema-con-espacios.md"


def test_all_pages_excludes_index_and_log(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("real-page", "Real", "content")
    pages = wm.all_pages()
    names = [p.name for p in pages]
    assert "index" not in names
    assert "log" not in names
    assert "real-page" in names


def test_append_log(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.append_log("ingest", "Fichero procesado")
    content = wm.log().raw
    assert "ingest" in content
    assert "Fichero procesado" in content


def test_frontmatter_parsing(wiki_dir):
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    page = wm.create_page("fm-test", "FM Test", "Cuerpo aquí.")
    fm = page.frontmatter
    assert fm["title"] == "FM Test"
    assert fm["status"] == "draft"
    assert fm["source_count"] == 1
