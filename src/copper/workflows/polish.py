"""
Polish workflow — health check for a coppermind.

The Archivist inspects the wiki for errors, contradictions,
orphan pages, and missing citations. Produces a lint report.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt

_POLISH_SYSTEM_PROMPT = "polish.archivist"


class PolishWorkflow:
    """Runs a health check on a coppermind's wiki."""

    def __init__(self, mind: CopperMind, llm: LLMBase):
        self.mind = mind
        self.llm = llm
        self.wiki = WikiManager(mind.wiki_dir)

    def run(self) -> PolishResult:
        context = _build_polish_context(self.wiki)
        prompt = _build_polish_prompt(self.mind.name, context)

        messages = [
            Message(role="system", content=render_prompt(_POLISH_SYSTEM_PROMPT)),
            Message(role="user", content=prompt),
        ]
        response = self.llm.complete(messages)

        # Save lint report
        date = datetime.now().strftime("%Y-%m-%d")
        report_path = self.mind.wiki_dir / f"lint-report-{date}.md"
        report_path.write_text(
            f"# Informe de Salud — {self.mind.name} ({date})\n\n{response.text}\n"
        )

        # Also run structural checks (no LLM needed)
        structural = _structural_checks(self.wiki)
        if structural:
            with open(report_path, "a") as f:
                f.write("\n## Comprobaciones estructurales\n\n")
                for check in structural:
                    f.write(f"- {check}\n")

        self.mind.append_log("polish", f"Informe de salud generado → {report_path.name}")

        return PolishResult(
            mind_name=self.mind.name,
            report_path=report_path,
            report_text=response.text,
            structural_issues=structural,
            tokens_used=response.tokens_used,
            cost_usd=response.cost_usd,
        )


def _build_polish_context(wiki: WikiManager) -> str:
    parts: list[str] = []
    parts.append(f"### Índice\n{wiki.read_index()}")
    for page in wiki.all_pages():
        parts.append(f"### {page.name}\n{page.raw}")
    return "\n\n".join(parts)


def _build_polish_prompt(mind_name: str, context: str) -> str:
    return render_prompt("polish.user", mind_name=mind_name, context=context)


def _structural_checks(wiki: WikiManager) -> list[str]:
    """Fast checks that don't need an LLM."""
    issues: list[str] = []
    all_pages = wiki.all_pages()
    all_slugs = {p.name for p in all_pages}

    for page in all_pages:
        # Pages with no content
        if len(page.body.strip()) < 50:
            issues.append(f"🟡 Página muy corta o vacía: `{page.name}`")

        # Pages without frontmatter
        if not page.frontmatter:
            issues.append(f"🟡 Sin frontmatter: `{page.name}`")

        # Pages without source citations
        if "[Fuente:" not in page.raw and "[Source:" not in page.raw:
            issues.append(f"🟡 Sin citas de fuente: `{page.name}`")

    # Check for orphan pages (not referenced in index)
    index_content = wiki.read_index()
    for slug in all_slugs:
        if slug not in index_content:
            issues.append(f"🟡 Página huérfana (no está en el índice): `{slug}`")

    return issues


class PolishResult:
    def __init__(
        self,
        mind_name: str,
        report_path: Path,
        report_text: str,
        structural_issues: list[str],
        tokens_used: int,
        cost_usd: float = 0.0,
    ):
        self.mind_name = mind_name
        self.report_path = report_path
        self.report_text = report_text
        self.structural_issues = structural_issues
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return (
            f"PolishResult(mind={self.mind_name!r}, "
            f"issues={len(self.structural_issues)}, tokens={self.tokens_used}, cost=${self.cost_usd:.6f})"
        )
