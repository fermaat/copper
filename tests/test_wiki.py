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


def test_source_to_slug_strips_extension():
    from copper.core.wiki import source_to_slug

    # Core invariant: citation with extension == path stem (no extension)
    assert source_to_slug("Mistborn.pdf") == source_to_slug("Mistborn")
    assert source_to_slug("Era Two.epub") == source_to_slug("Era Two")
    assert source_to_slug("notes.txt") == source_to_slug("notes")


def test_source_to_slug_handles_all_known_extensions():
    from copper.core.wiki import source_to_slug

    base = "my-source"
    for ext in (".pdf", ".txt", ".md", ".epub", ".docx", ".html"):
        assert source_to_slug(f"{base}{ext}") == base, f"failed for {ext}"
        assert source_to_slug(f"{base}{ext.upper()}") == base, f"failed for {ext.upper()}"


def test_source_to_slug_preserves_slugging():
    from copper.core.wiki import source_to_slug

    assert source_to_slug("The Final Empire.pdf") == "the-final-empire"
    assert source_to_slug("book_name") == "book-name"


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


# ------------------------------------------------------------------ #
# merge_page (FASE A3)                                                #
# ------------------------------------------------------------------ #


@pytest.fixture
def wiki_with_pages(wiki_dir):
    """Wiki with masting + misting pages and cross-links."""
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    (wiki_dir / "index.md").write_text(
        "# Índice\n\n- [[masting]] — Habilidad masting\n- [[misting]] — Habilidad misting\n"
    )
    wm.create_page(
        "masting",
        "Masting",
        "Una habilidad alomántica. Ver [[misting]]. [Fuente: libro.md]",
    )
    wm.create_page(
        "misting",
        "Misting",
        "Un Misting usa un solo metal. Ver también [[masting]]. [Fuente: libro.md]",
    )
    return wm


def test_merge_page_body_concatenated(wiki_with_pages):
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    dst = wm.page("misting")
    assert "Una habilidad alomántica" in dst.body
    assert "Un Misting usa un solo metal" in dst.body


def test_merge_page_src_deleted(wiki_with_pages):
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    assert not wm.page("masting").exists()


def test_merge_page_wikilinks_rewritten(wiki_with_pages):
    wm = wiki_with_pages
    # Add a third page that references masting
    wm.create_page("feruchemy", "Feruchemy", "Ver [[masting]] y [[misting]]. [Fuente: libro.md]")

    wm.merge_page("masting", "misting")

    third = wm.page("feruchemy")
    assert "[[masting]]" not in third.body
    assert third.body.count("[[misting]]") == 2  # original + rewritten


def test_merge_page_no_false_positive_partial_slug(wiki_with_pages):
    """[[masting-ritual]] must not be rewritten when merging masting → misting."""
    wm = wiki_with_pages
    wm.create_page("ritual", "Ritual", "Ver [[masting-ritual]] y [[masting]]. [Fuente: libro.md]")

    wm.merge_page("masting", "misting")

    ritual = wm.page("ritual")
    assert "[[masting-ritual]]" in ritual.body  # untouched
    assert "[[masting]]" not in ritual.body  # rewritten


def test_merge_page_no_self_links_in_dst(wiki_with_pages):
    """After merging masting→misting, dst should have no [[misting]] self-link."""
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    dst = wm.page("misting")
    import re

    assert not re.search(r"\[\[misting\]\]", dst.body)


def test_merge_page_log_entry(wiki_with_pages):
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    log_content = wm.log().raw
    assert "merge" in log_content
    assert "masting" in log_content
    assert "misting" in log_content


def test_merge_page_source_count_bumped(wiki_with_pages):
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    dst = wm.page("misting")
    assert dst.frontmatter.get("source_count", 0) == 2


def test_merge_page_index_cleaned(wiki_with_pages):
    wm = wiki_with_pages
    wm.merge_page("masting", "misting")

    index = wm.read_index()
    assert "masting" not in index


def test_merge_page_nonexistent_src_noop(wiki_dir):
    """merge_page with missing src should be a no-op (not raise)."""
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("misting", "Misting", "Contenido. [Fuente: libro.md]")

    # Should not raise
    wm.merge_page("ghost", "misting")
    assert wm.page("misting").exists()


def test_merge_page_nonexistent_dst_raises(wiki_dir):
    """merge_page with missing dst should raise ValueError."""
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("masting", "Masting", "Contenido. [Fuente: libro.md]")

    with pytest.raises(ValueError, match="non-existent"):
        wm.merge_page("masting", "ghost")


def test_merge_page_same_slug_raises(wiki_dir):
    """merge_page(x, x) should raise ValueError."""
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    wm.create_page("masting", "Masting", "Contenido. [Fuente: libro.md]")

    with pytest.raises(ValueError, match="itself"):
        wm.merge_page("masting", "masting")


def test_merge_page_chained(wiki_dir):
    """a→b followed by b→c should produce a single merged page c."""
    from copper.core.wiki import WikiManager

    wm = WikiManager(wiki_dir)
    (wiki_dir / "index.md").write_text("- [[aaa]] — a\n- [[bbb]] — b\n- [[ccc]] — c\n")
    wm.create_page("aaa", "Aaa", "Contenido A. [Fuente: x.md]")
    wm.create_page("bbb", "Bbb", "Contenido B. Ver [[aaa]]. [Fuente: x.md]")
    wm.create_page("ccc", "Ccc", "Contenido C. Ver [[bbb]]. [Fuente: x.md]")

    wm.merge_page("aaa", "bbb")  # a absorbed into b
    wm.merge_page("bbb", "ccc")  # b (now containing a) absorbed into c

    assert not wm.page("aaa").exists()
    assert not wm.page("bbb").exists()
    assert wm.page("ccc").exists()
    ccc = wm.page("ccc")
    assert "Contenido A" in ccc.body
    assert "Contenido B" in ccc.body
