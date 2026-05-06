"""
Functional tests — one per phase, showing the feature end-to-end.

Run with:
    pdm run pytest tests/test_functional.py -v -s

Each test prints a human-readable trace so you can verify the feature works
as intended, not just that assertions pass.
"""

import re
import pytest


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module
    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


def _parse_descend(text: str) -> list[str]:
    """Parse child names from a scanner <descend> response."""
    match = re.search(r"<descend>(.*?)</descend>", text, re.DOTALL)
    if not match:
        return []
    return [n.strip() for n in match.group(1).splitlines() if n.strip()]


# ------------------------------------------------------------------ #
# Phase 1 — Recursive CopperMind data model                          #
# ------------------------------------------------------------------ #


def test_phase1_tree_navigation(tmp_minds_dir):
    """
    A parent coppermind can forge children, discover them, and navigate
    the tree in both directions. A 3-level tree proves arbitrary depth.
    """
    from copper.core.coppermind import CopperMind

    print("\n=== Phase 1 — Árbol jerárquico de copperminds ===\n")

    aventura = CopperMind.forge("aventura", "Adventure module")
    fase1    = aventura.forge_child("fase-1", "La Convocatoria")
    fase2    = aventura.forge_child("fase-2", "El Bosque Maldito")
    enc      = fase1.forge_child("encuentro-inicial", "El encuentro en la taberna")

    fase1.meta_summary_path.write_text(
        "Fase 1: los héroes son convocados por el rey y viajan a Luthadel."
    )
    fase2.meta_summary_path.write_text(
        "Fase 2: el bosque entre las ciudades está maldito y debe ser cruzado."
    )

    print(f"{aventura.name}/  (is_root={aventura.is_root})")
    for child in aventura.children():
        print(f"  └── {child.name}/  (parent → '{child.parent.name}')")
        print(f"        _meta: \"{child.meta_summary[:60]}\"")
        for gc in child.children():
            print(f"        └── {gc.name}/  (parent → '{gc.parent.name}')")

    all_desc = [d.name for d in aventura.descendants()]
    print(f"\naventura.descendants() → {all_desc}")
    print(f"enc.parent.parent.name  → '{enc.parent.parent.name}'")
    print(f"enc.meta_summary        → '{enc.meta_summary}' (nunca escrito)")

    assert aventura.is_root
    assert not fase1.is_root
    assert fase1.parent.name == "aventura"
    assert enc.parent.parent.name == "aventura"
    assert {c.name for c in aventura.children()} == {"fase-1", "fase-2"}
    assert {d.name for d in aventura.descendants()} == {"fase-1", "fase-2", "encuentro-inicial"}
    assert "Luthadel" in fase1.meta_summary
    assert enc.meta_summary == ""

    print("\n✓ Árbol de 3 niveles, navegación bidireccional, meta_summary OK")


# ------------------------------------------------------------------ #
# Phase 2 — Tap with progressive disclosure                          #
# ------------------------------------------------------------------ #


def test_phase2_hierarchical_tap(tmp_minds_dir):
    """
    Tap on a 2-level coppermind routes the question through a scanner first.
    The scanner picks which sub-copperminds to search; the answer comes from there.
    A second query shows the 'parent only' path (no child descent).
    """
    from copper.core.coppermind import CopperMind
    from copper.core.wiki import WikiManager
    from copper.llm.mock import MockLLM
    from copper.workflows.tap import TapWorkflow

    print("\n=== Phase 2 — Tap jerárquico con scanner ===\n")

    # Build tree
    aventura = CopperMind.forge("aventura", "Adventure module")
    WikiManager(aventura.wiki_dir).create_page(
        "overview", "Overview",
        "Three-phase adventure. Heroes: Vin, Elend, Sazed. [Fuente: overview]",
    )
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    fase1.meta_summary_path.write_text("Fase 1: los héroes son convocados al palacio.")
    WikiManager(fase1.wiki_dir).create_page(
        "convocatoria", "La Convocatoria",
        "The king summons heroes to Luthadel before the new moon. [Fuente: module]",
    )
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    fase2.meta_summary_path.write_text("Fase 2: cruce del bosque maldito.")
    WikiManager(fase2.wiki_dir).create_page(
        "bosque", "El Bosque",
        "The cursed forest lies between the two cities. [Fuente: module]",
    )

    print("Árbol:")
    print(f"  {aventura.name}/  wiki/overview.md")
    for child in aventura.children():
        pages = [p.stem for p in child.wiki_dir.glob("*.md")
                 if p.name not in ("index.md", "log.md", "_meta.md")]
        print(f"    └── {child.name}/  wiki/{pages}")
        print(f"          _meta: \"{child.meta_summary}\"")

    # --- Query 1: scanner routes to fase-1 ---
    q1 = "¿Qué deben hacer los héroes en la fase 1?"
    print(f"\nPregunta 1: \"{q1}\"")

    llm1 = MockLLM([
        "<descend>\nfase-1\n</descend>",                                              # scanner
        "PAGE: convocatoria",                                                         # retriever
        "Los héroes deben viajar a Luthadel antes de la luna nueva. [Source: convocatoria]",  # answer
    ])
    result1 = TapWorkflow([aventura], llm1).run(q1)

    chosen1 = _parse_descend(llm1._responses[0])
    print(f"  [Scanner] eligió:    {chosen1}")
    print(f"  [Calls total]:       {llm1._call_count}  (scanner + retriever + respuesta)")
    print(f"  [Respuesta]:         {result1.answer}")

    assert chosen1 == ["fase-1"], f"Expected ['fase-1'], got {chosen1}"
    assert llm1._call_count == 3
    assert "Luthadel" in result1.answer

    # --- Query 2: scanner says parent context is enough ---
    q2 = "¿Quiénes son los personajes principales?"
    print(f"\nPregunta 2: \"{q2}\"")

    llm2 = MockLLM([
        "<descend>\n</descend>",                                        # scanner → parent only
        "Los personajes son Vin, Elend y Sazed. [Source: overview]",   # answer
    ])
    result2 = TapWorkflow([aventura], llm2).run(q2)

    chosen2 = _parse_descend(llm2._responses[0])
    print(f"  [Scanner] eligió:    {chosen2 or '(solo padre — sin descenso a hijos)'}")
    print(f"  [Calls total]:       {llm2._call_count}  (scanner + respuesta, sin retriever)")
    print(f"  [Respuesta]:         {result2.answer}")

    assert chosen2 == []
    assert llm2._call_count == 2
    assert "Vin" in result2.answer

    print("\n✓ Scanner enrutó a fase-1; camino 'solo padre' también funciona")


# ------------------------------------------------------------------ #
# Phase 3 — Recursive Polish                                         #
# ------------------------------------------------------------------ #


def test_phase3_recursive_polish(tmp_minds_dir):
    """
    Polishing a parent polishes all children first (bottom-up), then the
    parent itself. Every mind gets a fresh _meta.md after its pass.
    """
    from copper.core.coppermind import CopperMind
    from copper.core.wiki import WikiManager
    from copper.llm.mock import MockLLM
    from copper.workflows.polish import PolishWorkflow

    print("\n=== Phase 3 — Polish recursivo bottom-up ===\n")

    aventura = CopperMind.forge("aventura", "Adventure module")
    WikiManager(aventura.wiki_dir).create_page(
        "overview", "Overview",
        "Three-phase adventure. Heroes: Vin, Elend, Sazed. [Fuente: overview]",
    )
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    WikiManager(fase1.wiki_dir).create_page(
        "convocatoria", "La Convocatoria",
        "Heroes summoned to Luthadel before the new moon. [Fuente: module]",
    )
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    WikiManager(fase2.wiki_dir).create_page(
        "bosque", "El Bosque",
        "Cursed forest between the two cities. Strange creatures. [Fuente: module]",
    )

    print("Árbol antes del polish:")
    print(f"  {aventura.name}/  _meta existe: {aventura.meta_summary_path.exists()}")
    for child in aventura.children():
        print(f"    └── {child.name}/  _meta existe: {child.meta_summary_path.exists()}")

    # Even calls = archivist lint report, odd calls = _meta summary
    llm = MockLLM([
        "# Informe de salud\n\n🔵 Wiki en buen estado.",
        "Resumen generado para el scanner.",
    ])

    result = PolishWorkflow(aventura, llm).run()

    print("\nDespués del polish:")
    for child in aventura.children():
        lint = list(child.wiki_dir.glob("lint-report-*.md"))
        print(f"  {child.name}/")
        print(f"    lint-report: {'✓' if lint else '✗'}")
        print(f"    _meta.md:    {'✓' if child.meta_summary_path.exists() else '✗'}"
              f"  → \"{child.meta_summary[:55]}\"")
    lint_parent = list(aventura.wiki_dir.glob("lint-report-*.md"))
    print(f"  {aventura.name}/")
    print(f"    lint-report: {'✓' if lint_parent else '✗'}")
    print(f"    _meta.md:    {'✓' if aventura.meta_summary_path.exists() else '✗'}"
          f"  → \"{aventura.meta_summary[:55]}\"")

    print(f"\n[Calls total]: {llm._call_count}  (3 mentes × 2 llamadas cada una)")
    print(f"[children en result]: {[r.mind_name for r in result.children_results]}")

    assert fase1.meta_summary_path.exists()
    assert fase2.meta_summary_path.exists()
    assert aventura.meta_summary_path.exists()
    assert "Resumen generado" in fase1.meta_summary
    assert any(fase1.wiki_dir.glob("lint-report-*.md"))
    assert any(fase2.wiki_dir.glob("lint-report-*.md"))
    assert any(aventura.wiki_dir.glob("lint-report-*.md"))
    assert len(result.children_results) == 2
    assert llm._call_count == 6

    print("\n✓ Bottom-up: hijos primero, _meta.md fresco en todos los nodos")
