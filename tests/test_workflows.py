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
