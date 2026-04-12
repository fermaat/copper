"""
Copper CLI — The Archivist's interface.

  forge    → Create a new coppermind
  store    → Fill it with knowledge
  tap      → Extract knowledge (mono or multi-mind)
  polish   → Health check
  chat     → Interactive session
  list     → List all copperminds
  status   → Stats for a coppermind
  link     → Link two copperminds bidirectionally
  unlink   → Remove a link between copperminds
  graph    → Visualise the mind link graph
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich import print as rprint

from copper.core.coppermind import CopperMind
from copper.llm.base import LLMBase
from copper.workflows.store import StoreWorkflow
from copper.workflows.tap import TapWorkflow
from copper.workflows.polish import PolishWorkflow


app = typer.Typer(
    name="copper",
    help="[bold copper]Copper[/bold copper] — Mentecobres: knowledge stored in metal.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


# ------------------------------------------------------------------ #
# LLM loader                                                          #
# ------------------------------------------------------------------ #


def _load_llm() -> LLMBase:
    """
    Load the configured LLM. Tries core-llm-bridge first, falls back to MockLLM.
    Set COPPER_LLM_PROVIDER and COPPER_LLM_MODEL env vars to configure.
    """
    import os

    provider_name = os.getenv("COPPER_LLM_PROVIDER", "mock")
    model = os.getenv("COPPER_LLM_MODEL", "")

    if provider_name == "mock":
        from copper.llm.mock import MockLLM
        return MockLLM()

    try:
        from core_llm_bridge import BridgeEngine
        from core_llm_bridge.providers import create_provider
        from copper.llm.bridge_adapter import BridgeAdapter

        provider = create_provider(provider_name, **({"model": model} if model else {}))
        engine = BridgeEngine(provider=provider)
        return BridgeAdapter(engine)
    except ImportError:
        console.print("[yellow]⚠ core-llm-bridge no encontrado. Usando MockLLM.[/yellow]")
        from copper.llm.mock import MockLLM
        return MockLLM()


# ------------------------------------------------------------------ #
# Commands                                                            #
# ------------------------------------------------------------------ #


@app.command()
def forge(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
    topic: Annotated[str, typer.Option("--topic", "-t", help="Tema de conocimiento")] = "",
):
    """⚒  Forge a new coppermind."""
    if not topic:
        topic = typer.prompt("¿Sobre qué tema almacenará conocimiento esta mentecobre?")

    try:
        mind = CopperMind.forge(name, topic)
        console.print(Panel(
            f"[bold green]Mentecobre forjada:[/bold green] [cyan]{name}[/cyan]\n"
            f"[dim]Tema:[/dim] {topic}\n"
            f"[dim]Ubicación:[/dim] {mind.path}\n\n"
            f"[dim]La memoria espera ser llenada.[/dim]\n"
            f"[dim]Almacena conocimiento con:[/dim] [bold]copper store {name} <fichero>[/bold]",
            title="[copper]⚒ Forja completa[/copper]",
            border_style="yellow",
        ))
    except FileExistsError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)


@app.command()
def store(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
    source: Annotated[Optional[Path], typer.Argument(help="Fichero a almacenar")] = None,
    all_raw: Annotated[bool, typer.Option("--all", help="Procesar todos los ficheros en raw/")] = False,
):
    """📥  Store knowledge into a coppermind (fill it)."""
    try:
        mind = CopperMind.get(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    llm = _load_llm()
    workflow = StoreWorkflow(mind, llm)

    sources: list[Path] = []
    if all_raw:
        sources = mind.raw_files()
        if not sources:
            console.print(f"[yellow]⚠ No hay ficheros en raw/ de '{name}'.[/yellow]")
            raise typer.Exit(0)
    elif source:
        # If path is relative, try looking inside raw/ first
        if not source.is_absolute() and not source.exists():
            candidate = mind.raw_dir / source
            if candidate.exists():
                source = candidate
        sources = [source]
    else:
        console.print("[red]✗ Indica un fichero o usa --all.[/red]")
        raise typer.Exit(1)

    for src in sources:
        with console.status(f"[cyan]Almacenando '{src.name}' en la mentecobre...[/cyan]"):
            try:
                result = workflow.run(src)
            except FileNotFoundError as e:
                console.print(f"[red]✗ {e}[/red]")
                continue

        console.print(
            f"[green]✓[/green] [bold]{src.name}[/bold] almacenado → "
            f"[cyan]{len(result.pages_written)}[/cyan] páginas wiki actualizadas"
        )
        if result.pages_written:
            for p in result.pages_written:
                console.print(f"  [dim]· {p}[/dim]")


@app.command()
def tap(
    names: Annotated[str, typer.Argument(
        help="Nombre(s) de mentecobre (separados por coma) o --all"
    )],
    question: Annotated[str, typer.Argument(help="Pregunta a responder")],
    save: Annotated[bool, typer.Option("--save", "-s", help="Guardar respuesta en outputs/")] = False,
    with_links: Annotated[bool, typer.Option("--with-links", "-l", help="Incluir también las mentecobres enlazadas")] = False,
):
    """🔍  Tap a coppermind (extract knowledge)."""
    try:
        minds = CopperMind.resolve_many(names)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    if with_links:
        seen = {m.name for m in minds}
        extra = []
        for m in list(minds):
            for linked in m.linked_minds():
                if linked.name not in seen:
                    seen.add(linked.name)
                    extra.append(linked)
        if extra:
            console.print(f"[dim]+ Mentecobres enlazadas: {', '.join(m.name for m in extra)}[/dim]")
            minds = minds + extra

    llm = _load_llm()
    workflow = TapWorkflow(minds, llm)

    mind_list = ", ".join(m.name for m in minds)
    with console.status(f"[cyan]Extrayendo de [{mind_list}]...[/cyan]"):
        result = workflow.run(question, save_to_outputs=save)

    console.print(Panel(
        Markdown(result.answer),
        title=f"[cyan]💡 {question[:60]}[/cyan]",
        border_style="blue",
    ))

    if result.connections:
        console.print("\n[bold yellow]🔗 Conexiones detectadas:[/bold yellow]")
        for conn in result.connections:
            console.print(f"  {conn}")

    if result.saved_to:
        for path in result.saved_to:
            console.print(f"[dim]💾 Guardado en: {path}[/dim]")


@app.command()
def polish(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
):
    """🪙  Polish a coppermind (health check)."""
    try:
        mind = CopperMind.get(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    llm = _load_llm()
    workflow = PolishWorkflow(mind, llm)

    with console.status("[cyan]El Archivista inspecciona la mentecobre...[/cyan]"):
        result = workflow.run()

    console.print(Panel(
        Markdown(result.report_text),
        title=f"[yellow]🪙 Informe de salud — {name}[/yellow]",
        border_style="yellow",
    ))

    if result.structural_issues:
        console.print("\n[bold]Comprobaciones estructurales:[/bold]")
        for issue in result.structural_issues:
            console.print(f"  {issue}")

    console.print(f"\n[dim]Informe guardado en: {result.report_path}[/dim]")


@app.command(name="list")
def list_minds():
    """📋  List all copperminds."""
    minds = CopperMind.list_all()

    if not minds:
        console.print(
            "[yellow]No hay mentecobres. Crea una con:[/yellow] [bold]copper forge <nombre>[/bold]"
        )
        return

    table = Table(title="Mentecobres", border_style="yellow", header_style="bold cyan")
    table.add_column("Nombre", style="cyan")
    table.add_column("Tema")
    table.add_column("Fuentes", justify="right")
    table.add_column("Páginas wiki", justify="right")
    table.add_column("Creada", style="dim")

    for mind in minds:
        stats = mind.stats()
        table.add_row(
            mind.name,
            stats["topic"],
            str(stats["raw_sources"]),
            str(stats["wiki_pages"]),
            mind.config.created[:10],
        )

    console.print(table)


@app.command()
def status(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
):
    """📊  Show stats for a coppermind."""
    try:
        mind = CopperMind.get(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    stats = mind.stats()

    console.print(Panel(
        f"[bold]Tema:[/bold] {stats['topic']}\n"
        f"[bold]Fuentes en raw/:[/bold] {stats['raw_sources']}\n"
        f"[bold]Páginas wiki:[/bold] {stats['wiki_pages']}\n"
        f"[bold]Mentecobres enlazadas:[/bold] {', '.join(stats['linked_minds']) or 'ninguna'}\n"
        f"[bold]Ubicación:[/bold] [dim]{mind.path}[/dim]\n"
        f"[bold]Creada:[/bold] {mind.config.created[:10]}",
        title=f"[cyan]📊 {name}[/cyan]",
        border_style="cyan",
    ))


@app.command()
def chat(
    names: Annotated[str, typer.Argument(
        help="Nombre(s) de mentecobre o --all"
    )],
    with_links: Annotated[bool, typer.Option("--with-links", "-l", help="Incluir mentecobres enlazadas")] = False,
):
    """💬  Interactive chat with coppermind(s)."""
    try:
        minds = CopperMind.resolve_many(names)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    if with_links:
        seen = {m.name for m in minds}
        extra = []
        for m in list(minds):
            for linked in m.linked_minds():
                if linked.name not in seen:
                    seen.add(linked.name)
                    extra.append(linked)
        if extra:
            minds = minds + extra

    llm = _load_llm()
    workflow = TapWorkflow(minds, llm)
    mind_list = ", ".join(m.name for m in minds)

    console.print(Panel(
        f"Conectado a: [cyan]{mind_list}[/cyan]\n"
        "[dim]Escribe tu pregunta. Comandos: /save /exit[/dim]",
        title="[copper]💬 Sesión de extracción[/copper]",
        border_style="yellow",
    ))

    while True:
        try:
            question = typer.prompt("\n[tú]")
        except (KeyboardInterrupt, EOFError):
            break

        if question.strip() == "/exit":
            break

        save = question.strip().endswith("/save")
        question = question.replace("/save", "").strip()

        if not question:
            continue

        with console.status("[cyan]El Archivista consulta la memoria...[/cyan]"):
            result = workflow.run(question, save_to_outputs=save)

        console.print(Panel(Markdown(result.answer), border_style="blue"))

        if result.saved_to:
            console.print(f"[dim]💾 {result.saved_to[0]}[/dim]")

    console.print("[dim]La memoria permanece. Hasta la próxima.[/dim]")


@app.command()
def link(
    name_a: Annotated[str, typer.Argument(help="Primera mentecobre")],
    name_b: Annotated[str, typer.Argument(help="Segunda mentecobre")],
):
    """🔗  Link two copperminds bidirectionally."""
    try:
        mind_a = CopperMind.get(name_a)
        mind_b = CopperMind.get(name_b)
        mind_a.link(mind_b)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] [cyan]{name_a}[/cyan] [dim]⟷[/dim] [cyan]{name_b}[/cyan] enlazadas.\n"
        f"[dim]Usa `copper tap {name_a},{name_b}` o `copper tap {name_a} --with-links` para consultarlas juntas.[/dim]"
    )


@app.command()
def unlink(
    name_a: Annotated[str, typer.Argument(help="Primera mentecobre")],
    name_b: Annotated[str, typer.Argument(help="Segunda mentecobre")],
):
    """✂  Unlink two copperminds."""
    try:
        mind_a = CopperMind.get(name_a)
        mind_b = CopperMind.get(name_b)
        mind_a.unlink(mind_b)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[yellow]✓[/yellow] Enlace entre [cyan]{name_a}[/cyan] y [cyan]{name_b}[/cyan] eliminado.")


@app.command()
def graph():
    """🕸  Visualise the coppermind link graph."""
    minds = CopperMind.list_all()
    if not minds:
        console.print("[yellow]No hay mentecobres.[/yellow]")
        return

    tree = Tree("🕸  [bold yellow]Red de Mentecobres[/bold yellow]")
    rendered: set[str] = set()

    # Nodes with links first
    linked_minds = [m for m in minds if m.config.linked_minds]
    solo_minds = [m for m in minds if not m.config.linked_minds]

    for mind in linked_minds:
        if mind.name in rendered:
            continue
        branch = tree.add(f"[cyan]{mind.name}[/cyan] [dim]({mind.config.topic})[/dim]")
        rendered.add(mind.name)
        for linked_name in mind.config.linked_minds:
            branch.add(f"[green]⟷[/green] [cyan]{linked_name}[/cyan]")

    if solo_minds:
        solo_branch = tree.add("[dim]Sin enlaces[/dim]")
        for mind in solo_minds:
            solo_branch.add(f"[dim]{mind.name}[/dim] [dim]({mind.config.topic})[/dim]")

    console.print(tree)
    console.print(
        f"\n[dim]{len(minds)} mentecobre(s) · "
        f"{sum(len(m.config.linked_minds) for m in minds) // 2} enlace(s)[/dim]"
    )
