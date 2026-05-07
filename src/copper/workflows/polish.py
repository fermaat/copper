"""
Polish workflow — health check for a coppermind.

The Archivist inspects the wiki for errors, contradictions,
orphan pages, and missing citations. Produces a lint report.

For hierarchical copperminds, polish runs bottom-up: children are polished
first (post-order), then the parent. After each mind is polished its
wiki/_meta.md is regenerated so the parent scanner has an up-to-date summary.

Out of scope (deferred to phase 6.5): moving content between siblings.
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

    def run(self, max_depth: int | None = None) -> PolishResult:
        """Polish this mind and, recursively, all its descendants.

        Post-order traversal: each child is fully polished (and its _meta.md
        refreshed) before the parent runs its own polish pass.

        *max_depth* caps recursion: 0 = only this mind, 1 = this mind + direct
        children, None = unlimited. The CLI flag --depth N wires this in phase 6.
        """
        # --- Bottom-up: recurse into children first ---
        children_results: list[PolishResult] = []
        if max_depth is None or max_depth > 0:
            child_depth = None if max_depth is None else max_depth - 1
            for child in self.mind.children():
                child_wf = PolishWorkflow(child, self.llm)
                children_results.append(child_wf.run(max_depth=child_depth))

        # --- Polish this mind (existing logic) ---
        context = _build_polish_context(self.wiki)
        prompt = _build_polish_prompt(self.mind.name, context)

        messages = [
            Message(role="system", content=render_prompt(_POLISH_SYSTEM_PROMPT)),
            Message(role="user", content=prompt),
        ]
        response = self.llm.complete(messages)

        date = datetime.now().strftime("%Y-%m-%d")
        report_path = self.mind.wiki_dir / f"lint-report-{date}.md"
        report_path.write_text(
            f"# Informe de Salud — {self.mind.name} ({date})\n\n{response.text}\n"
        )

        structural = _structural_checks(self.wiki)
        if structural:
            with open(report_path, "a") as f:
                f.write("\n## Comprobaciones estructurales\n\n")
                for check in structural:
                    f.write(f"- {check}\n")

        self.mind.append_log("polish", f"Informe de salud generado → {report_path.name}")

        # --- Regenerate _meta.md for this mind ---
        meta_text = self._generate_meta(context)
        self.mind.meta_summary_path.write_text(meta_text)

        total_tokens = response.tokens_used + sum(r.tokens_used for r in children_results)
        total_cost = response.cost_usd + sum(r.cost_usd for r in children_results)

        return PolishResult(
            mind_name=self.mind.name,
            report_path=report_path,
            report_text=response.text,
            structural_issues=structural,
            tokens_used=total_tokens,
            cost_usd=total_cost,
            children_results=children_results,
            meta_path=self.mind.meta_summary_path,
        )

    def _generate_meta(self, context: str) -> str:
        """Generate a fresh _meta.md summary for this mind."""
        user_content = render_prompt(
            "polish.meta",
            mind_name=self.mind.name,
            topic=self.mind.config.topic,
            context=context,
        )
        messages = [
            Message(role="system", content=render_prompt(_POLISH_SYSTEM_PROMPT)),
            Message(role="user", content=user_content),
        ]
        return self.llm.complete(messages).text


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
        if len(page.body.strip()) < 50:
            issues.append(f"🟡 Página muy corta o vacía: `{page.name}`")

        if not page.frontmatter:
            issues.append(f"🟡 Sin frontmatter: `{page.name}`")

        if "[Fuente:" not in page.raw and "[Source:" not in page.raw:
            issues.append(f"🟡 Sin citas de fuente: `{page.name}`")

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
        children_results: list["PolishResult"] | None = None,
        meta_path: Path | None = None,
    ):
        self.mind_name = mind_name
        self.report_path = report_path
        self.report_text = report_text
        self.structural_issues = structural_issues
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd
        self.children_results = children_results or []
        self.meta_path = meta_path

    def __repr__(self) -> str:
        return (
            f"PolishResult(mind={self.mind_name!r}, "
            f"issues={len(self.structural_issues)}, "
            f"children={len(self.children_results)}, "
            f"tokens={self.tokens_used}, cost=${self.cost_usd:.6f})"
        )
