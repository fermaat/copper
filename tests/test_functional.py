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

    print(aventura.format_tree())
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

    print(aventura.format_tree())

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
    print(aventura.format_tree())

    # Even calls = archivist lint report, odd calls = _meta summary
    llm = MockLLM([
        "# Informe de salud\n\n🔵 Wiki en buen estado.",
        "Resumen generado para el scanner.",
    ])

    result = PolishWorkflow(aventura, llm).run()

    print("\nDespués del polish:")
    print(aventura.format_tree())

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


# ------------------------------------------------------------------ #
# Phase 4 — Store with routing                                       #
# ------------------------------------------------------------------ #


def test_phase4_store_routing(tmp_minds_dir, tmp_path):
    """
    Storing content in a hierarchical coppermind first asks a router LLM
    where the content belongs. Content lands in the right child (or back at
    the parent for cross-cutting material), and a new child can be forged
    on the fly if the router decides so.
    """
    from copper.core.coppermind import CopperMind
    from copper.llm.mock import MockLLM
    from copper.workflows.store import StoreWorkflow

    def wiki_xml(slug):
        return (
            "<wiki_updates>"
            f'<page slug="{slug}" title="{slug.title()}" action="create">'
            f"<content>Contenido sobre {slug}. [Fuente: src]</content>"
            "</page>"
            f"<index># Índice\n\n- [[{slug}]]</index>"
            "</wiki_updates>"
        )

    print("\n=== Phase 4 — Store con enrutamiento ===\n")

    aventura = CopperMind.forge("aventura", "adventure module")
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    fase1.meta_summary_path.write_text("Fase 1: convocatoria y viaje a Luthadel.")
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    fase2.meta_summary_path.write_text("Fase 2: cruce del bosque maldito.")

    print("Árbol inicial:")
    print(aventura.format_tree())

    # --- Source 1: router picks fase-1 ---
    src1 = tmp_path / "convocatoria.md"
    src1.write_text("# La Convocatoria\n\nEl rey convoca a los héroes a Luthadel.\n")

    llm1 = MockLLM(["<route>fase-1</route>", wiki_xml("convocatoria")])
    result1 = StoreWorkflow(aventura, llm1).run(src1)

    print(f"\nFuente 1: '{src1.name}'")
    print(f"  [Router] → '{result1.routed_to}'")
    print(f"  [Calls]  {llm1._call_count}  (router + archivist)")
    print(f"  [Páginas escritas] {result1.pages_written} en '{result1.routed_to}'")

    assert result1.routed_to == "fase-1"
    assert llm1._call_count == 2
    assert len(fase1.wiki_pages()) > 0
    assert len(aventura.wiki_pages()) == 0

    # --- Source 2: router says parent (cross-cutting content) ---
    src2 = tmp_path / "personajes.md"
    src2.write_text("# Personajes\n\nVin, Elend y Sazed aparecen en todas las fases.\n")

    llm2 = MockLLM(["<route>parent</route>", wiki_xml("personajes")])
    result2 = StoreWorkflow(aventura, llm2).run(src2)

    print(f"\nFuente 2: '{src2.name}'")
    print(f"  [Router] → '{result2.routed_to or 'padre (contenido transversal)'}'")
    print(f"  [Calls]  {llm2._call_count}")
    print(f"  [Páginas escritas] {result2.pages_written} en 'aventura'")

    assert result2.routed_to is None
    assert llm2._call_count == 2
    assert len(aventura.wiki_pages()) > 0

    # --- Source 3: router proposes a new child ---
    src3 = tmp_path / "epilogo.md"
    src3.write_text("# Epílogo\n\nTras derrotar al Lord Ruler, los héroes reconstruyen el Imperio.\n")

    llm3 = MockLLM([
        "<route>new_child:epilogo</route>\n<topic>Epílogo y reconstrucción del Imperio</topic>",
        wiki_xml("epilogo"),
    ])
    result3 = StoreWorkflow(aventura, llm3).run(src3)

    print(f"\nFuente 3: '{src3.name}'")
    print(f"  [Router] → nuevo hijo '{result3.routed_to}' (forjado en el momento)")
    print(f"  [Calls]  {llm3._call_count}")
    child_names = {c.name for c in aventura.children()}
    print("  [Hijos ahora]")
    print(aventura.format_tree())

    assert result3.routed_to == "epilogo"
    assert llm3._call_count == 2
    assert "epilogo" in child_names

    print("\n✓ Enrutamiento a hijo, a padre y creación de hijo nuevo — todos OK")
