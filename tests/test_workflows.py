"""Integration tests for Store, Tap, and Polish workflows using MockLLM."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def mind(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    return CopperMind.forge("test-mind", "inteligencia artificial")


@pytest.fixture
def source_file(tmp_path):
    f = tmp_path / "articulo.md"
    f.write_text(
        "# Transformers\n\n"
        "Los transformers usan mecanismos de atención para procesar secuencias. "
        "Introducidos por Vaswani et al. en 2017.\n"
    )
    return f


# ------------------------------------------------------------------ #
# Store                                                               #
# ------------------------------------------------------------------ #


class TestStoreWorkflow:
    def test_store_basic(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            [
                "<wiki_updates>"
                '<page slug="transformers" title="Transformers" action="create">'
                "<content>Los transformers usan atención. [Fuente: articulo.md]</content>"
                "</page>"
                "<index># Índice\n\n- [[transformers]] — Arquitectura transformer</index>"
                "</wiki_updates>"
            ]
        )
        workflow = StoreWorkflow(mind, llm)
        result = workflow.run(source_file)

        assert result.source == "articulo.md"
        assert "transformers" in result.pages_written
        assert llm._call_count == 1

    def test_store_creates_wiki_page(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            [
                "<wiki_updates>"
                '<page slug="resumen" title="Resumen" action="create">'
                "<content>Resumen de la fuente.</content>"
                "</page>"
                "<index># Índice actualizado</index>"
                "</wiki_updates>"
            ]
        )
        workflow = StoreWorkflow(mind, llm)
        workflow.run(source_file)

        assert (mind.wiki_dir / "resumen.md").exists()

    def test_store_updates_log(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM()
        workflow = StoreWorkflow(mind, llm)
        workflow.run(source_file)

        log_content = mind.log_path.read_text()
        assert "store" in log_content
        assert "articulo.md" in log_content

    def test_store_fallback_when_no_xml(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta sin formato XML"])
        workflow = StoreWorkflow(mind, llm)
        result = workflow.run(source_file)

        # Fallback should still create a page
        assert len(result.pages_written) > 0

    def test_store_missing_file_raises(self, mind):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM()
        workflow = StoreWorkflow(mind, llm)
        with pytest.raises(FileNotFoundError):
            workflow.run(Path("/no/existe.md"))


# ------------------------------------------------------------------ #
# Routed Store — Phase 4                                             #
# ------------------------------------------------------------------ #


class TestRoutedStore:
    """Router decides where new content lands in a hierarchical coppermind."""

    def _wiki_xml(self, slug="resumen", title="Resumen"):
        return (
            "<wiki_updates>"
            f'<page slug="{slug}" title="{title}" action="create">'
            f"<content>Contenido de {slug}. [Fuente: src]</content>"
            "</page>"
            f"<index># Índice\n\n- [[{slug}]]</index>"
            "</wiki_updates>"
        )

    def test_store_flat_router_not_invoked(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM([self._wiki_xml()])
        result = StoreWorkflow(mind, llm).run(source_file)

        assert result.routed_to is None
        assert llm._call_count == 1  # archivist only — no router

    def test_store_routes_to_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([
            "<route>fase-1</route>",   # router → fase-1
            self._wiki_xml(),          # archivist in fase-1
        ])
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to == "fase-1"
        assert llm._call_count == 2
        # Page landed in the child's wiki, not the parent's
        child = parent.children()[0]
        assert len(child.wiki_pages()) > 0
        assert len(parent.wiki_pages()) == 0

    def test_store_routes_to_parent(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([
            "<route>parent</route>",  # router → stay at parent
            self._wiki_xml(),
        ])
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to is None
        assert llm._call_count == 2
        assert len(parent.wiki_pages()) > 0

    def test_store_router_creates_new_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([
            "<route>new_child:fase-2</route>\n<topic>Segunda fase del módulo</topic>",
            self._wiki_xml(),
        ])
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to == "fase-2"
        assert llm._call_count == 2
        child_names = {c.name for c in parent.children()}
        assert "fase-2" in child_names

    def test_store_no_route_flag_skips_router(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([self._wiki_xml()])
        result = StoreWorkflow(parent, llm).run(source_file, no_route=True)

        assert result.routed_to is None
        assert llm._call_count == 1  # no router call

    def test_store_into_explicit_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([self._wiki_xml()])
        result = StoreWorkflow(parent, llm).run(source_file, into="fase-1")

        assert result.routed_to == "fase-1"
        assert llm._call_count == 1  # no router, direct to child


# ------------------------------------------------------------------ #
# Tap                                                                 #
# ------------------------------------------------------------------ #


class TestTapWorkflow:
    def test_tap_returns_answer(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            ["Los transformers son arquitecturas de redes neuronales. [Fuente: transformers]"]
        )
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Qué son los transformers?")

        assert "transformers" in result.answer.lower()
        assert result.minds_used == ["test-mind"]

    def test_tap_multi_mind(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        mind1 = CopperMind.forge("mente-a", "tema A")
        mind2 = CopperMind.forge("mente-b", "tema B")

        llm = MockLLM(["Respuesta combinada de A y B."])
        workflow = TapWorkflow([mind1, mind2], llm)
        result = workflow.run("pregunta")

        assert set(result.minds_used) == {"mente-a", "mente-b"}
        # 2 phase-1 selection calls (one per mind) + 1 phase-2 answer call
        assert llm._call_count == 3

    def test_tap_save_to_outputs(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta guardada."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Pregunta de prueba?", save_to_outputs=True)

        assert len(result.saved_to) == 1
        assert result.saved_to[0].exists()
        assert "Respuesta guardada" in result.saved_to[0].read_text()

    def test_tap_updates_log(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta."])
        workflow = TapWorkflow([mind], llm)
        workflow.run("pregunta log")

        log_content = mind.log_path.read_text()
        assert "tap" in log_content

    def test_tap_with_history_passes_prior_turns(self, mind):
        from copper.llm.base import Message
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        llm = MockLLM(["Respuesta de seguimiento."])
        history = [
            Message(role="user", content="¿Qué son los transformers?"),
            Message(role="assistant", content="Son arquitecturas de atención."),
        ]
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Cuándo se introdujeron?", history=history)

        assert result.answer == "Respuesta de seguimiento."
        # The LLM must receive system + 2 history turns + current user = 4 messages
        last_call = llm.calls[-1]
        assert len(last_call) == 4
        assert last_call[1].role == "user"
        assert last_call[1].content == history[0].content
        assert last_call[2].role == "assistant"
        assert last_call[3].role == "user"

    def test_tap_without_history_unchanged(self, mind):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        llm = MockLLM(["Respuesta normal."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Pregunta simple?")

        # system + user only (no history)
        last_call = llm.calls[-1]
        assert len(last_call) == 2
        assert last_call[0].role == "system"
        assert last_call[1].role == "user"


# ------------------------------------------------------------------ #
# Hierarchical Tap — Phase 2                                          #
# ------------------------------------------------------------------ #


class TestHierarchicalTap:
    """Scanner-guided two-stage tap on hierarchical copperminds."""

    def _make_tree(self, tmp_minds_dir, children: list[str] | None = None):
        """Return (parent, {child_name: child}) with simple wiki pages."""
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager

        parent = CopperMind.forge("aventura", "adventure module")
        WikiManager(parent.wiki_dir).create_page(
            "overview", "Overview", "General adventure overview. [Fuente: overview]"
        )

        child_map = {}
        for name in (children or []):
            child = parent.forge_child(name, f"topic for {name}")
            WikiManager(child.wiki_dir).create_page(
                f"pagina-{name}", f"Page {name}", f"Content of {name}. [Fuente: src]"
            )
            child_map[name] = child

        return parent, child_map

    def test_flat_mind_scanner_not_invoked(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("plana", "flat mind")
        WikiManager(mind.wiki_dir).create_page("resumen", "Resumen", "Info. [Fuente: src]")
        llm = MockLLM(["PAGE: resumen", "Respuesta plana."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿pregunta?")

        assert result.answer == "Respuesta plana."
        # flat path: 1 retriever call + 1 answer = 2
        assert llm._call_count == 2

    def test_hierarchical_scanner_picks_one_child(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, children = self._make_tree(tmp_minds_dir, ["fase-1", "fase-2"])
        llm = MockLLM([
            "<descend>\nfase-1\n</descend>",  # scanner → pick fase-1
            "PAGE: pagina-fase-1",            # retriever on fase-1
            "Respuesta sobre fase-1.",        # final answer
        ])
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿qué pasa en la fase 1?")

        assert result.answer == "Respuesta sobre fase-1."
        # 1 scanner + 1 retriever on fase-1 + 1 answer
        assert llm._call_count == 3
        # Context in the answer call includes the child's page
        answer_call_user = llm.calls[-1][-1].content
        assert "fase-1" in answer_call_user

    def test_hierarchical_scanner_picks_two_children(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, _ = self._make_tree(tmp_minds_dir, ["fase-1", "fase-2"])
        llm = MockLLM([
            "<descend>\nfase-1\nfase-2\n</descend>",  # scanner → both
            "PAGE: pagina-fase-1",                     # retriever on fase-1
            "PAGE: pagina-fase-2",                     # retriever on fase-2
            "Respuesta que abarca ambas fases.",        # final answer
        ])
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿resumen de todas las fases?")

        assert result.answer == "Respuesta que abarca ambas fases."
        # 1 scanner + 2 retrievers + 1 answer
        assert llm._call_count == 4

    def test_hierarchical_scanner_parent_only(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, _ = self._make_tree(tmp_minds_dir, ["fase-1"])
        llm = MockLLM([
            "<descend>\n</descend>",  # scanner → parent only
            "Respuesta solo del padre.",
        ])
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿pregunta general?")

        assert result.answer == "Respuesta solo del padre."
        # 1 scanner + 0 retrievers + 1 answer
        assert llm._call_count == 2
        # No child pages in context
        answer_call_user = llm.calls[-1][-1].content
        assert "fase-1" not in answer_call_user

    def test_hierarchical_three_level_nested_scan(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        root = CopperMind.forge("root", "root topic")
        child = root.forge_child("child", "child topic")
        grandchild = child.forge_child("grandchild", "grandchild topic")
        WikiManager(grandchild.wiki_dir).create_page(
            "deep-page", "Deep Page", "Deep content. [Fuente: deep]"
        )

        llm = MockLLM([
            "<descend>\nchild\n</descend>",       # scanner on root
            "<descend>\ngrandchild\n</descend>",  # scanner on child
            "PAGE: deep-page",                    # retriever on grandchild
            "Respuesta profunda.",                # final answer
        ])
        workflow = TapWorkflow([root], llm)
        result = workflow.run("¿contenido profundo?")

        assert result.answer == "Respuesta profunda."
        # 2 scanners + 1 retriever + 1 answer
        assert llm._call_count == 4


# ------------------------------------------------------------------ #
# Polish                                                              #
# ------------------------------------------------------------------ #


class TestPolishWorkflow:
    def test_polish_generates_report(self, mind):
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["# Informe\n\n🔵 Wiki en buen estado."])
        workflow = PolishWorkflow(mind, llm)
        result = workflow.run()

        assert result.report_path.exists()
        assert "Informe" in result.report_path.read_text()

    def test_polish_structural_checks(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        # Create a page without source citations
        wm = WikiManager(mind.wiki_dir)
        wm.create_page("sin-fuente", "Sin Fuente", "Contenido sin citas.")

        llm = MockLLM(["Informe mock."])
        workflow = PolishWorkflow(mind, llm)
        result = workflow.run()

        assert any("sin-fuente" in issue for issue in result.structural_issues)

    def test_polish_updates_log(self, mind):
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Informe."])
        workflow = PolishWorkflow(mind, llm)
        workflow.run()

        log_content = mind.log_path.read_text()
        assert "polish" in log_content

    def test_polish_flat_creates_meta(self, mind):
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Informe.", "Meta summary of this mind."])
        result = PolishWorkflow(mind, llm).run()

        assert mind.meta_summary_path.exists()
        assert mind.meta_summary == "Meta summary of this mind."
        assert result.meta_path == mind.meta_summary_path
        # flat mind: 1 archivist call + 1 meta call = 2
        assert llm._call_count == 2

    def test_polish_hierarchical_bottom_up(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "parent topic")
        child1 = parent.forge_child("hijo-1", "child 1 topic")
        child2 = parent.forge_child("hijo-2", "child 2 topic")
        for mind in (child1, child2, parent):
            WikiManager(mind.wiki_dir).create_page(
                "info", "Info", f"Content of {mind.name}. [Fuente: src]"
            )

        # Cycle: archivist responses on even calls, meta responses on odd
        llm = MockLLM(["# Informe.", "Meta generada."])
        result = PolishWorkflow(parent, llm).run()

        # All three minds have _meta.md
        assert child1.meta_summary_path.exists()
        assert child2.meta_summary_path.exists()
        assert parent.meta_summary_path.exists()

        # All three have lint reports
        assert any(parent.wiki_dir.glob("lint-report-*.md"))
        assert any(child1.wiki_dir.glob("lint-report-*.md"))
        assert any(child2.wiki_dir.glob("lint-report-*.md"))

        # Result tree
        assert len(result.children_results) == 2
        child_names = {r.mind_name for r in result.children_results}
        assert child_names == {"hijo-1", "hijo-2"}

        # 3 minds × 2 calls each = 6 total
        assert llm._call_count == 6

    def test_polish_idempotent(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        WikiManager(mind.wiki_dir).create_page("info", "Info", "Content. [Fuente: src]")
        llm = MockLLM(["# Informe.", "Meta summary."])

        PolishWorkflow(mind, llm).run()
        meta_after_first = mind.meta_summary

        llm2 = MockLLM(["# Informe.", "Meta summary."])
        PolishWorkflow(mind, llm2).run()
        meta_after_second = mind.meta_summary

        # _meta.md is overwritten, not appended — same content, same length
        assert meta_after_first == meta_after_second

    def test_polish_max_depth_zero_skips_children(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "topic")
        parent.forge_child("hijo", "child topic")

        llm = MockLLM(["# Informe.", "Meta."])
        PolishWorkflow(parent, llm).run(max_depth=0)

        # max_depth=0: only parent polished, child untouched
        assert not parent.children()[0].meta_summary_path.exists()
        assert llm._call_count == 2  # archivist + meta for parent only
