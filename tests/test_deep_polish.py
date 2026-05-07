"""Unit tests for Phase 6.5 — Deep structural polish."""

from __future__ import annotations

from pathlib import Path

import pytest

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.mock import MockLLM
from copper.workflows.deep_polish import (
    DeepPolishPlan,
    DeepPolishWorkflow,
    EntityEntry,
    MoveAction,
    TransversalAction,
    _filter_candidates,
    _parse_entities,
    _parse_plan,
)


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


# ── Parser tests ──────────────────────────────────────────────────────────────


def test_parse_entities_extracts_all():
    xml = """
    <entities>
      <entity name="Szeth" kind="character" pages="szeth,flashbacks,oath"/>
      <entity name="Alethkar" kind="location" pages="intro,map"/>
    </entities>
    """
    result = _parse_entities(xml)
    assert len(result) == 2
    assert result[0] == ("Szeth", "character", ["szeth", "flashbacks", "oath"])
    assert result[1] == ("Alethkar", "location", ["intro", "map"])


def test_parse_entities_empty():
    assert _parse_entities("<entities/>") == []
    assert _parse_entities("no xml here") == []


def test_parse_plan_moves_and_transversals():
    xml = """
    <plan>
      <move slug="szeth" from="part-1" to="part-2" reason="belongs there"/>
      <transversal entity="Szeth" kind="character" children="part-1,part-2" reason="spans both"/>
    </plan>
    """
    plan = _parse_plan(xml)
    assert len(plan.moves) == 1
    assert plan.moves[0] == MoveAction(
        slug="szeth", from_child="part-1", to_child="part-2", reason="belongs there"
    )
    assert len(plan.transversals) == 1
    t = plan.transversals[0]
    assert t.entity == "Szeth"
    assert t.source_children == ["part-1", "part-2"]


def test_parse_plan_empty():
    plan = _parse_plan("<plan/>")
    assert plan.is_empty
    plan2 = _parse_plan("no xml")
    assert plan2.is_empty


# ── Threshold filter ──────────────────────────────────────────────────────────


def test_filter_candidates_respects_min_children(monkeypatch):
    monkeypatch.setattr("copper.workflows.deep_polish.settings.copper_deep_polish_min_children", 2)
    monkeypatch.setattr("copper.workflows.deep_polish.settings.copper_deep_polish_min_pages", 2)

    entries = [
        EntityEntry(name="Szeth", kind="character", child_name="part-1", pages=["p1", "p2"]),
        EntityEntry(name="Szeth", kind="character", child_name="part-2", pages=["p3"]),
        EntityEntry(name="Kaladin", kind="character", child_name="part-1", pages=["k1", "k2"]),
        # Kaladin only in 1 child → filtered out
    ]
    result = _filter_candidates(entries)
    names = {e.name for e in result}
    assert "Szeth" in names
    assert "Kaladin" not in names


def test_filter_candidates_respects_min_pages(monkeypatch):
    monkeypatch.setattr("copper.workflows.deep_polish.settings.copper_deep_polish_min_children", 2)
    monkeypatch.setattr("copper.workflows.deep_polish.settings.copper_deep_polish_min_pages", 4)

    # Szeth appears in 2 children but only 3 total pages → filtered out
    entries = [
        EntityEntry(name="Szeth", kind="character", child_name="part-1", pages=["p1", "p2"]),
        EntityEntry(name="Szeth", kind="character", child_name="part-2", pages=["p3"]),
    ]
    result = _filter_candidates(entries)
    assert result == []


# ── Workflow tests ────────────────────────────────────────────────────────────


def test_deep_polish_no_children_returns_empty(tmp_minds_dir):
    mind = CopperMind.forge("solo", "a single mind with no children")
    llm = MockLLM([])
    result = DeepPolishWorkflow(mind, llm).run(dry_run=True, auto_yes=True)
    assert result.plan.is_empty
    assert result.moves_applied == []
    assert result.tokens_used == 0


def test_dry_run_does_not_move_pages(tmp_minds_dir):
    parent = CopperMind.forge("aventura", "Adventure")
    child1 = parent.forge_child("parte-1", "La llegada")
    child2 = parent.forge_child("parte-2", "El viaje")

    child1.wiki.create_page("szeth", "Szeth", "Szeth-son-son-Vallano is a Windrunner.")
    child1.wiki.create_page("lore", "Lore", "Background lore for Szeth.")
    # child2 gets a page so both children trigger mapper calls
    child2.wiki.create_page("dalinar", "Dalinar", "Dalinar Kholin.")

    mapper1 = '<entities><entity name="Szeth" kind="character" pages="szeth,lore"/></entities>'
    mapper2 = "<entities/>"
    reducer_response = '<plan><move slug="szeth" from="parte-1" to="parte-2" reason="test"/></plan>'

    llm = MockLLM([mapper1, mapper2, reducer_response])
    result = DeepPolishWorkflow(parent, llm).run(dry_run=True, auto_yes=True)

    assert len(result.plan.moves) == 1
    assert result.moves_applied == []
    # Page still exists in parte-1 after dry-run
    assert child1.wiki.page("szeth").exists()


def test_apply_move_relocates_page(tmp_minds_dir):
    parent = CopperMind.forge("aventura", "Adventure")
    child1 = parent.forge_child("parte-1", "La llegada")
    child2 = parent.forge_child("parte-2", "El viaje")

    child1.wiki.create_page("szeth", "Szeth", "Szeth content here.")
    child1.wiki.create_page("lore", "Lore", "Szeth lore here.")
    child2.wiki.create_page("dalinar", "Dalinar", "Dalinar Kholin.")

    mapper1 = '<entities><entity name="Szeth" kind="character" pages="szeth,lore"/></entities>'
    mapper2 = "<entities/>"
    reducer = '<plan><move slug="szeth" from="parte-1" to="parte-2" reason="fits better"/></plan>'
    spine = "Updated overview content."

    llm = MockLLM([mapper1, mapper2, reducer, spine])
    result = DeepPolishWorkflow(parent, llm).run(dry_run=False, auto_yes=True)

    assert len(result.moves_applied) == 1
    assert not child1.wiki.page("szeth").exists()
    assert child2.wiki.page("szeth").exists()


def test_caps_limit_moves(tmp_minds_dir, monkeypatch):
    monkeypatch.setattr("copper.workflows.deep_polish.settings.copper_deep_polish_max_moves", 2)

    parent = CopperMind.forge("aventura", "Adventure")
    child1 = parent.forge_child("parte-1", "La llegada")
    child2 = parent.forge_child("parte-2", "El viaje")

    child1.wiki.create_page("p", "P", "content")
    child2.wiki.create_page("q", "Q", "content")

    # Reducer proposes 5 moves — only 2 should be kept after cap
    moves_xml = "".join(
        f'<move slug="page-{i}" from="parte-1" to="parte-2" reason="r"/>' for i in range(5)
    )
    reducer = f"<plan>{moves_xml}</plan>"
    llm = MockLLM(["<entities/>", "<entities/>", reducer])

    result = DeepPolishWorkflow(parent, llm).run(dry_run=True, auto_yes=True)
    assert len(result.plan.moves) == 2


def test_synthesizer_creates_parent_page(tmp_minds_dir):
    parent = CopperMind.forge("aventura", "Adventure")
    child1 = parent.forge_child("parte-1", "La llegada")
    child2 = parent.forge_child("parte-2", "El viaje")

    child1.wiki.create_page("szeth", "Szeth", "Szeth appears in parte-1.")
    child2.wiki.create_page("szeth-arc", "Szeth Arc", "Szeth arc continues in parte-2.")

    mapper1 = '<entities><entity name="Szeth" kind="character" pages="szeth"/></entities>'
    mapper2 = '<entities><entity name="Szeth" kind="character" pages="szeth-arc"/></entities>'
    reducer = '<plan><transversal entity="Szeth" kind="character" children="parte-1,parte-2" reason="spans both"/></plan>'
    synth_body = "## Szeth\n\nSzeth-son-son-Vallano is a central character..."
    spine = "Updated overview."

    # synth only receives fragments from pages where entity name matches (case-insensitive).
    # Neither "szeth-arc" slug has entity "Szeth" in entries because mapper2 page slug is "szeth-arc".
    # Both entries share entity name "Szeth" so both source pages will be grabbed.
    llm = MockLLM([mapper1, mapper2, reducer, synth_body, spine])
    result = DeepPolishWorkflow(parent, llm).run(dry_run=False, auto_yes=True)

    assert len(result.transversals_applied) == 1
    assert parent.wiki.page("szeth").exists()
