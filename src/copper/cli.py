"""
Copper CLI — The Archivist's interface.

  forge    → Create a new coppermind
  store    → Fill it with knowledge
  tap      → Extract knowledge
  polish   → Health check
  chat     → Interactive session
  list     → List all copperminds
  status   → Stats for a coppermind
  link     → Link two copperminds (Phase 2)
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
):
    """🔍  Tap a coppermind (extract knowledge)."""
    try:
        minds = CopperMind.resolve_many(names)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

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
):
    """💬  Interactive chat with coppermind(s)."""
    try:
        minds = CopperMind.resolve_many(names)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

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
