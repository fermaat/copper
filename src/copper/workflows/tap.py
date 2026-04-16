"""
Tap workflow — extract knowledge from the coppermind.

Reads the wiki (compiled knowledge) to answer a question.
Supports querying one or multiple copperminds simultaneously.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, LLMResponse, Message


TAP_SYSTEM = """\
Eres el Archivista de una o varias mentecobres. Respondes preguntas basándote
exclusivamente en el contenido del wiki compilado. Citas las páginas del wiki
que informan tu respuesta con [Fuente: nombre-pagina].

Cuando consultas varias mentecobres:
- Busca activamente conexiones, paralelismos o contradicciones entre ellas.
- Marca las conexiones encontradas con [Conexión: mente-a ↔ mente-b: descripción].
- Si la pregunta sólo aplica a algunas mentes, indícalo claramente.

Si la respuesta revela insights nuevos, ofreces guardarlos en el wiki.
"""


class TapWorkflow:
    """Answers a question using the compiled wiki."""

    def __init__(self, minds: list[CopperMind], llm: LLMBase):
        self.minds = minds
        self.llm = llm

    def run(self, question: str, save_to_outputs: bool = False) -> TapResult:
        # Gather context from all selected minds
        mind_names = ", ".join(m.name for m in self.minds)
        logger.info(f"[tap] Pregunta: '{question[:80]}' | mentes: [{mind_names}]")

        context = _build_context(self.minds)
        multi = len(self.minds) > 1
        prompt = _build_tap_prompt(context, question, multi=multi)

        logger.info(f"[tap] Contexto: {len(context):,} chars | prompt total: {len(prompt):,} chars")
        logger.info("[tap] Enviando al LLM...")
        messages = [
            Message(role="system", content=TAP_SYSTEM),
            Message(role="user", content=prompt),
        ]
        response = self.llm.complete(messages)
        logger.info(f"[tap] LLM respondió ({response.tokens_used} tokens)")

        # Extract detected cross-mind connections from the response
        connections = _extract_connections(response.text)

        # Optionally persist the answer to outputs/
        saved_to: list[Path] = []
        if save_to_outputs:
            saved_to = _save_to_outputs(question, response, self.minds)

        # Log in each mind
        for mind in self.minds:
            mind.append_log("tap", f"Consulta: {question[:80]}")

        return TapResult(
            question=question,
            answer=response.text,
            minds_used=[m.name for m in self.minds],
            tokens_used=response.tokens_used,
            saved_to=saved_to,
            connections=connections,
        )


def _build_context(minds: list[CopperMind]) -> str:
    parts: list[str] = []
    for mind in minds:
        wiki = WikiManager(mind.wiki_dir)
        index = wiki.read_index()
        pages = wiki.all_pages()

        parts.append(f"## Mentecobre: {mind.name} (tema: {mind.config.topic})")
        parts.append(f"### Índice\n{index}")

        for page in pages:
            parts.append(f"### Página: {page.name}\n{page.raw}")

    return "\n\n".join(parts)


def _build_tap_prompt(context: str, question: str, multi: bool = False) -> str:
    cross_mind_instructions = (
        "\n\nEstás consultando VARIAS mentecobres. Además de responder, busca activamente:\n"
        "- Conceptos compartidos entre mentes distintas\n"
        "- Contradicciones o tensiones entre ellas\n"
        "- Conexiones no obvias que enriquezcan la respuesta\n"
        "Márcalas como: [Conexión: mente-a ↔ mente-b: descripción breve]\n"
    ) if multi else ""

    return f"""\
## Contenido del wiki
{context}
---
## Pregunta
{question}{cross_mind_instructions}
Responde basándote únicamente en el contenido del wiki anterior.
Cita las páginas que usas con [Fuente: nombre-pagina].
"""


def _extract_connections(text: str) -> list[str]:
    """Parse [Conexión: ...] markers from the LLM response."""
    import re
    return re.findall(r"\[Conexión:[^\]]+\]", text)


def _save_to_outputs(question: str, response: LLMResponse, minds: list[CopperMind]) -> list[Path]:
    from datetime import datetime

    date = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = question[:40].lower().replace(" ", "-").replace("?", "")
    filename = f"{date}_{slug}.md"

    content = f"# {question}\n\n{response.text}\n"
    saved: list[Path] = []

    for mind in minds:
        out_path = mind.outputs_dir / filename
        out_path.write_text(content)
        saved.append(out_path)

    return saved


class TapResult:
    def __init__(
        self,
        question: str,
        answer: str,
        minds_used: list[str],
        tokens_used: int,
        saved_to: list[Path],
        connections: list[str] | None = None,
    ):
        self.question = question
        self.answer = answer
        self.minds_used = minds_used
        self.tokens_used = tokens_used
        self.saved_to = saved_to
        self.connections = connections or []

    def __repr__(self) -> str:
        return (
            f"TapResult(minds={self.minds_used}, "
            f"connections={len(self.connections)}, "
            f"tokens={self.tokens_used}, saved={len(self.saved_to)})"
        )
