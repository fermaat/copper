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
from rich.theme import Theme
from rich.tree import Tree
from rich import print as rprint

from copper.core.coppermind import CopperMind
from copper.api.deps import get_ingest_describer, get_store_llm, get_tap_llm
from copper.workflows.store import StoreResult, StoreWorkflow
from copper.workflows.tap import TapWorkflow
from copper.workflows.polish import PolishWorkflow

# Theme so cosmere-flavour tokens highlight consistently in the terminal.
# Keep this in sync with the `.cosmere` CSS class used by the web UI.
_COSMERE_THEME = Theme(
    {
        "copper": "#b87333",
        "cosmere": "bold #b87333",
    }
)

app = typer.Typer(
    name="copper",
    help="[bold copper]Copper[/bold copper] — Mentecobres: knowledge stored in metal.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console(theme=_COSMERE_THEME)


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
        console.print(
            Panel(
                f"[bold green]Mentecobre forjada:[/bold green] [cyan]{name}[/cyan]\n"
                f"[dim]Tema:[/dim] {topic}\n"
                f"[dim]Ubicación:[/dim] {mind.path}\n\n"
                f"[dim]La memoria espera ser llenada.[/dim]\n"
                f"[dim]Almacena conocimiento con:[/dim] [bold]copper store {name} <fichero>[/bold]",
                title="[copper]⚒ Forja completa[/copper]",
                border_style="yellow",
            )
        )
    except FileExistsError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)


@app.command()
def store(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
    source: Annotated[Optional[Path], typer.Argument(help="Fichero a almacenar")] = None,
    all_raw: Annotated[
        bool, typer.Option("--all", help="Procesar todos los ficheros en raw/")
    ] = False,
):
    """📥  Store knowledge into a coppermind (fill it)."""
    try:
        mind = CopperMind.get(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    llm = get_store_llm(mind)
    describer = get_ingest_describer(mind)
    workflow = StoreWorkflow(mind, llm, image_describer=describer)

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
        # Warn if the file is being copied from outside raw/
        raw_path = mind.raw_dir / src.name
        if src.resolve() != raw_path.resolve():
            console.print(f"[dim]→ Copiando a raw/{src.name}[/dim]")

        with console.status(
            f"[cyan]Almacenando '{src.name}' en la [copper]mentecobre[/copper]...[/cyan]"
        ):
            try:
                result = workflow.run(src)
            except FileNotFoundError as e:
                console.print(f"[red]✗ {e}[/red]")
                continue

        cost_str = f" · [dim]${result.cost_usd:.4f}[/dim]" if result.cost_usd else ""
        console.print(
            f"[green]✓[/green] [bold]{src.name}[/bold] almacenado → "
            f"[cyan]{len(result.pages_written)}[/cyan] páginas wiki actualizadas{cost_str}"
        )
        if result.pages_written:
            for p in result.pages_written:
                console.print(f"  [dim]· {p}[/dim]")


@app.command()
def tap(
    names: Annotated[
        str, typer.Argument(help="Nombre(s) de mentecobre (separados por coma) o --all")
    ],
    question: Annotated[str, typer.Argument(help="Pregunta a responder")],
    save: Annotated[
        bool, typer.Option("--save", "-s", help="Guardar respuesta en outputs/")
    ] = False,
    with_links: Annotated[
        bool, typer.Option("--with-links", "-l", help="Incluir también las mentecobres enlazadas")
    ] = False,
    personality: Annotated[
        Optional[str],
        typer.Option(
            "--personality",
            "-p",
            help="Personalidad (p.ej. tap.gamemaster, tap.scholar). Ver `copper personalities`.",
        ),
    ] = None,
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

    llm = get_tap_llm(minds[0])
    workflow = TapWorkflow(minds, llm, personality=personality)

    mind_list = ", ".join(m.name for m in minds)
    with console.status(
        f"[cyan][copper]Tapping[/copper] [{mind_list}] — [copper]assaying[/copper] then forging answer...[/cyan]"
    ):
        result = workflow.run(question, save_to_outputs=save)

    console.print(
        Panel(
            Markdown(result.answer),
            title=f"[cyan]💡 {question[:60]}[/cyan]",
            border_style="blue",
        )
    )

    if result.connections:
        console.print("\n[bold yellow]🔗 Conexiones detectadas:[/bold yellow]")
        for conn in result.connections:
            console.print(f"  {conn}")

    if result.saved_to:
        for path in result.saved_to:
            console.print(f"[dim]💾 Guardado en: {path}[/dim]")

    if result.cost_usd:
        console.print(f"[dim]💰 Coste: ${result.cost_usd:.4f}[/dim]")


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

    llm = get_store_llm(mind)
    workflow = PolishWorkflow(mind, llm)

    with console.status(
        "[cyan]El [copper]Archivista[/copper] inspecciona la [copper]mentecobre[/copper]...[/cyan]"
    ):
        result = workflow.run()

    console.print(
        Panel(
            Markdown(result.report_text),
            title=f"[yellow]🪙 Informe de salud — {name}[/yellow]",
            border_style="yellow",
        )
    )

    if result.structural_issues:
        console.print("\n[bold]Comprobaciones estructurales:[/bold]")
        for issue in result.structural_issues:
            console.print(f"  {issue}")

    cost_str = f" · ${result.cost_usd:.4f}" if result.cost_usd else ""
    console.print(f"\n[dim]Informe guardado en: {result.report_path}{cost_str}[/dim]")


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

    console.print(
        Panel(
            f"[bold]Tema:[/bold] {stats['topic']}\n"
            f"[bold]Fuentes en raw/:[/bold] {stats['raw_sources']}\n"
            f"[bold]Páginas wiki:[/bold] {stats['wiki_pages']}\n"
            f"[bold]Mentecobres enlazadas:[/bold] {', '.join(stats['linked_minds']) or 'ninguna'}\n"
            f"[bold]Ubicación:[/bold] [dim]{mind.path}[/dim]\n"
            f"[bold]Creada:[/bold] {mind.config.created[:10]}",
            title=f"[cyan]📊 {name}[/cyan]",
            border_style="cyan",
        )
    )


@app.command()
def chat(
    names: Annotated[str, typer.Argument(help="Nombre(s) de mentecobre o --all")],
    with_links: Annotated[
        bool, typer.Option("--with-links", "-l", help="Incluir mentecobres enlazadas")
    ] = False,
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

    llm = get_tap_llm(minds[0])
    workflow = TapWorkflow(minds, llm)
    mind_list = ", ".join(m.name for m in minds)

    console.print(
        Panel(
            f"Conectado a: [cyan]{mind_list}[/cyan]\n"
            "[dim]Escribe tu pregunta. Comandos: /save /exit[/dim]",
            title="[copper]💬 Sesión de extracción[/copper]",
            border_style="yellow",
        )
    )

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

    console.print(
        f"[yellow]✓[/yellow] Enlace entre [cyan]{name_a}[/cyan] y [cyan]{name_b}[/cyan] eliminado."
    )


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


@app.command()
def watch(
    name: Annotated[str, typer.Argument(help="Nombre de la mentecobre")],
):
    """👁  Watch raw/ and auto-ingest new files as they arrive."""
    try:
        mind = CopperMind.get(name)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    llm = get_store_llm(mind)

    console.print(
        Panel(
            f"[bold]Mentecobre:[/bold] [cyan]{name}[/cyan]\n"
            f"[bold]Observando:[/bold] [dim]{mind.raw_dir}[/dim]\n\n"
            "[dim]Copia o mueve ficheros a raw/ para almacenarlos automáticamente.\n"
            "Ctrl+C para salir.[/dim]",
            title="[copper]👁 Archivista en guardia[/copper]",
            border_style="yellow",
        )
    )

    def _on_result(path: Path, result: StoreResult) -> None:
        cost_str = f" · [dim]${result.cost_usd:.4f}[/dim]" if result.cost_usd else ""
        console.print(
            f"[green]✓[/green] [bold]{result.source}[/bold] almacenado → "
            f"[cyan]{len(result.pages_written)}[/cyan] páginas wiki actualizadas{cost_str}"
        )
        for p in result.pages_written:
            console.print(f"  [dim]· {p}[/dim]")

    def _on_error(path: Path, exc: Exception) -> None:
        console.print(f"[red]✗ Error procesando '{path.name}': {exc}[/red]")

    try:
        from copper.watch import watch_raw_dir

        watch_raw_dir(mind, llm, on_result=_on_result, on_error=_on_error)
    except ImportError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)

    console.print("[dim]El Archivista descansa. Hasta la próxima.[/dim]")


@app.command()
def personalities():
    """🎭  List available tap personalities."""
    from copper.prompts import get_prompt_manager, list_prompts

    names = list_prompts(prefix="tap.")
    if not names:
        console.print("[yellow]No tap personalities registered.[/yellow]")
        raise typer.Exit(0)

    manager = get_prompt_manager()
    table = Table(title="[copper]Tap personalities[/copper]", border_style="yellow")
    table.add_column("Name", style="cyan")
    table.add_column("Preview", style="dim")
    for name in names:
        tpl = manager.get(name)
        preview = (tpl.template_str.strip().splitlines() or [""])[0][:80] if tpl else ""
        table.add_row(name, preview)
    console.print(table)
    console.print('\n[dim]Use with: [bold]copper tap <mind> "<question>" -p <name>[/bold][/dim]')


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes")] = False,
):
    """🌐  Start the Copper API server."""
    try:
        import uvicorn
        from copper.api.app import create_app
    except ImportError:
        console.print(
            "[red]✗ FastAPI/uvicorn not installed.[/red]\n"
            "[dim]Install the api extra: [bold]pdm install -G api[/bold][/dim]"
        )
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]API:[/bold]  http://{host}:{port}\n"
            f"[bold]Docs:[/bold] http://{host}:{port}/api/docs\n"
            f"[bold]UI:[/bold]   http://{host}:{port}/\n\n"
            "[dim]The Archivist awaits. Press Ctrl+C to stop.[/dim]",
            title="[copper]🌐 Copper API[/copper]",
            border_style="yellow",
        )
    )

    app_instance = create_app()
    uvicorn.run(app_instance, host=host, port=port, reload=reload)
