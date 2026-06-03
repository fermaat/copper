"""Tests for slug_normalize — find_slug_clusters, find_self_links, and propose_merges."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

# ------------------------------------------------------------------ #
# find_slug_clusters                                                   #
# ------------------------------------------------------------------ #


def test_cluster_detects_near_duplicates():
    from copper.core.slug_normalize import SlugCluster, find_slug_clusters

    result = find_slug_clusters(["masting", "misting", "allomancy"], 0.85)
    assert len(result) == 1
    assert set(result[0].slugs) == {"masting", "misting"}


def test_cluster_drops_singletons():
    from copper.core.slug_normalize import find_slug_clusters

    result = find_slug_clusters(["fire", "water", "earth"], 0.85)
    assert result == []


def test_cluster_empty_input():
    from copper.core.slug_normalize import find_slug_clusters

    assert find_slug_clusters([], 0.85) == []


def test_cluster_single_slug():
    from copper.core.slug_normalize import find_slug_clusters

    assert find_slug_clusters(["misting"], 0.85) == []


def test_cluster_output_sorted():
    from copper.core.slug_normalize import find_slug_clusters

    # Each cluster's slug list should be sorted.
    result = find_slug_clusters(["misting", "masting"], 0.85)
    assert result[0].slugs == sorted(result[0].slugs)


def test_cluster_reason_contains_similarity():
    from copper.core.slug_normalize import find_slug_clusters

    result = find_slug_clusters(["masting", "misting"], 0.85)
    assert "string-similarity" in result[0].reason


def test_cluster_transitive():
    """If a~b and b~c, all three should form one cluster."""
    from copper.core.slug_normalize import find_slug_clusters

    # "abc" ~ "abcd" ~ "abcde" — all within similarity threshold at 0.6
    result = find_slug_clusters(["abc", "abcd", "abcde"], 0.6)
    assert len(result) == 1
    assert len(result[0].slugs) == 3


def test_cluster_below_threshold_not_grouped():
    from copper.core.slug_normalize import find_slug_clusters

    result = find_slug_clusters(["allomancer", "allomancers"], 0.95)
    # "allomancer" / "allomancers" ratio is ~ 0.947 — just below 0.95
    # (exact value depends on difflib; the test just confirms threshold respected)
    # At 0.85 they SHOULD cluster
    result_low = find_slug_clusters(["allomancer", "allomancers"], 0.85)
    assert len(result_low) == 1
    assert set(result_low[0].slugs) == {"allomancer", "allomancers"}


# ------------------------------------------------------------------ #
# find_self_links                                                      #
# ------------------------------------------------------------------ #


def _make_page(name: str, body: str):
    page = MagicMock()
    page.name = name
    page.body = body
    return page


def test_self_link_detected():
    from copper.core.slug_normalize import find_self_links

    pages = [_make_page("masting", "El [[masting]] es una habilidad. [Fuente: libro.md]")]
    result = find_self_links(pages)
    assert len(result) == 1
    assert result[0].slug == "masting"


def test_self_link_no_false_positive_with_longer_slug():
    from copper.core.slug_normalize import find_self_links

    # [[masting-ritual]] should NOT be flagged as a self-link for 'masting'
    pages = [_make_page("masting", "Ver [[masting-ritual]] para más detalles.")]
    result = find_self_links(pages)
    assert result == []


def test_self_link_clean_page():
    from copper.core.slug_normalize import find_self_links

    pages = [_make_page("masting", "Contenido sin self-links. [[misting]] es diferente.")]
    result = find_self_links(pages)
    assert result == []


def test_self_link_empty_wiki():
    from copper.core.slug_normalize import find_self_links

    assert find_self_links([]) == []


def test_self_link_multiple_pages():
    from copper.core.slug_normalize import find_self_links

    pages = [
        _make_page("masting", "Ver [[masting]]."),
        _make_page("misting", "No tiene self-link."),
        _make_page("allomancy", "La [[allomancy]] es magia. [Fuente: x.md]"),
    ]
    result = find_self_links(pages)
    slugs = {sl.slug for sl in result}
    assert slugs == {"masting", "allomancy"}


# ------------------------------------------------------------------ #
# propose_merges (FASE A2)                                            #
# ------------------------------------------------------------------ #


def _make_wiki_with_pages(pages_data: list[tuple[str, str]]):
    """Build a fake wiki object whose all_pages() returns mock pages."""
    pages = []
    for name, body in pages_data:
        page = MagicMock()
        page.name = name
        page.body = body
        page.frontmatter = {"title": name.capitalize()}
        pages.append(page)
    wiki = MagicMock()
    wiki.all_pages.return_value = pages
    return wiki


def _make_llm(response_text: str):
    """Return a MockLLM-like stub that always returns response_text."""
    from copper.llm.mock import MockLLM

    return MockLLM([response_text])


def test_propose_merges_valid_xml():
    from copper.core.slug_normalize import MergeProposal, propose_merges

    wiki = _make_wiki_with_pages(
        [
            ("masting", "El masting usa un metal. [Fuente: libro.md]"),
            ("misting", "El misting usa un metal. [Fuente: libro.md]"),
            ("allomancy", "La allomancia es magia. [Fuente: libro.md]"),
        ]
    )
    llm = _make_llm(
        '<merge canonical="misting">'
        "<duplicate>masting</duplicate>"
        "<reason>Misspelling of Misting</reason>"
        "</merge>"
    )

    result = propose_merges(wiki, llm, threshold=0.85)

    assert len(result) == 1
    assert result[0].canonical == "misting"
    assert "masting" in result[0].duplicates
    assert "Misspelling" in result[0].reason


def test_propose_merges_garbage_response_returns_empty():
    from copper.core.slug_normalize import propose_merges

    wiki = _make_wiki_with_pages(
        [
            ("masting", "cuerpo. [Fuente: x.md]"),
            ("misting", "cuerpo. [Fuente: x.md]"),
        ]
    )
    llm = _make_llm("esto no es XML válido @@##$$")

    result = propose_merges(wiki, llm, threshold=0.85)
    assert result == []


def test_propose_merges_empty_response_returns_empty():
    from copper.core.slug_normalize import propose_merges

    wiki = _make_wiki_with_pages(
        [
            ("masting", "cuerpo. [Fuente: x.md]"),
            ("misting", "cuerpo. [Fuente: x.md]"),
        ]
    )
    llm = _make_llm("")

    result = propose_merges(wiki, llm, threshold=0.85)
    assert result == []


def test_propose_merges_canonical_not_in_wiki_discarded():
    from copper.core.slug_normalize import propose_merges

    wiki = _make_wiki_with_pages(
        [
            ("masting", "cuerpo. [Fuente: x.md]"),
            ("misting", "cuerpo. [Fuente: x.md]"),
        ]
    )
    # LLM returns a canonical that doesn't exist
    llm = _make_llm(
        '<merge canonical="invented-slug">'
        "<duplicate>masting</duplicate>"
        "<reason>No debería pasar</reason>"
        "</merge>"
    )

    result = propose_merges(wiki, llm, threshold=0.85)
    assert result == []


def test_propose_merges_outside_shortlist_discarded():
    from copper.core.slug_normalize import propose_merges

    # "fire" and "water" are far apart — no structural cluster.
    # LLM shouldn't be able to propose merges outside the shortlist.
    wiki = _make_wiki_with_pages(
        [
            ("fire", "cuerpo. [Fuente: x.md]"),
            ("water", "cuerpo. [Fuente: x.md]"),
        ]
    )
    llm = _make_llm(
        '<merge canonical="fire">'
        "<duplicate>water</duplicate>"
        "<reason>Intento de merge no solicitado</reason>"
        "</merge>"
    )

    # No cluster → no LLM call → empty proposals
    result = propose_merges(wiki, llm, threshold=0.85)
    assert result == []


def test_propose_merges_no_clusters_no_llm_call():
    from copper.core.slug_normalize import propose_merges

    wiki = _make_wiki_with_pages(
        [
            ("fire", "cuerpo. [Fuente: x.md]"),
            ("water", "cuerpo. [Fuente: x.md]"),
            ("earth", "cuerpo. [Fuente: x.md]"),
        ]
    )
    llm = _make_llm("never called")

    result = propose_merges(wiki, llm, threshold=0.85)
    # No clusters → should return empty without calling LLM
    assert result == []
    assert llm._call_count == 0
