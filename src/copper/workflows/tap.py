"""
Tap workflow — extraer (tapping the coppermind).

Reads the wiki (compiled knowledge) to answer a question.
Supports querying one or multiple copperminds simultaneously.
"""

from __future__ import annotations

from pathlib import Path

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, LLMResponse, Message


TAP_SYSTEM = """\
Eres el Archivista de una o varias mentecobres. Respondes preguntas basándote
exclusivamente en el contenido del wiki compilado. Citas las páginas del wiki
que informan tu respuesta con [Fuente: nombre-pagina].
Si la respuesta revela conexiones nuevas, lo señalas y ofreces guardarla en el wiki.
"""


class TapWorkflow:
    """Answers a question using the compiled wiki."""

    def __init__(self, minds: list[CopperMind], llm: LLMBase):
        self.minds = minds
        self.llm = llm

    def run(self, question: str, save_to_outputs: bool = False) -> TapResult:
        # Gather context from all selected minds
        context = _build_context(self.minds)
        prompt = _build_tap_prompt(context, question)

        messages = [
            Message(role="system", content=TAP_SYSTEM),
            Message(role="user", content=prompt),
        ]
        response = self.llm.complete(messages)

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


def _build_tap_prompt(context: str, question: str) -> str:
    return f"""\
## Contenido del wiki

{context}

---

## Pregunta

{question}

Responde basándote únicamente en el contenido del wiki anterior.
Cita las páginas que usas con [Fuente: nombre-pagina].
Si detectas conexiones nuevas entre mentecobres, márcalas con [Conexión: mente1 ↔ mente2].
"""


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
    ):
        self.question = question
        self.answer = answer
        self.minds_used = minds_used
        self.tokens_used = tokens_used
        self.saved_to = saved_to

    def __repr__(self) -> str:
        return (
            f"TapResult(minds={self.minds_used}, "
            f"tokens={self.tokens_used}, saved={len(self.saved_to)})"
        )
