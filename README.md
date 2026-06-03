# Copper · v2.2.1

AI-maintained knowledge bases, inspired by Karpathy's wiki concept and the Cosmere's *copperminds* — repositories of pure knowledge, maintained by a Feruchemical Archivist.

You feed Copper raw sources (articles, notes, transcripts, PDFs, Obsidian vaults). The Archivist (an LLM) reads each source and compiles it into a structured markdown wiki. No embeddings, no RAG, no vector databases — just folders and text files you can read, edit, and version-control yourself.

---

## Concepts

| Term | Meaning |
|---|---|
| **coppermind** | A folder-based knowledge base for a single topic |
| **store** | Ingest a source file; the Archivist updates the wiki |
| **tap** | Ask a question; the Archivist answers from the wiki |
| **polish** | Audit the wiki for gaps, stubs, and contradictions |
| **forge** | Create a new coppermind |

Multiple copperminds can be **linked** so that a `tap` query draws from several wikis at once.

Copperminds can also be **nested**: a parent coppermind contains child copperminds, each specialising in a sub-topic. The Archivist routes sources to the right child, and `tap` queries descend the tree to find the best answer.

| Term | Meaning |
|---|---|
| **sub-coppermind** | A child coppermind nested inside a parent |
| **forge child** | `copper forge parent/child` — creates a nested coppermind |
| **deep polish** | Three-pass map-reduce that reorganises pages across the tree |

---

## Installation

Requires Python 3.12+. Dependencies are managed with [PDM](https://pdm-project.org).

```bash
# Install dependencies
pdm install

# Optional extras
pdm install -G pdf    # PDF ingestion (pdfplumber)
pdm install -G watch  # Auto-ingest file watcher (watchdog)
pdm install -G llm    # Real LLM provider via core-llm-bridge
```

The `copper` CLI is registered as a project script:

```bash
pdm run copper --help
# or, after activating the venv:
copper --help
```

---

## Quickstart

```bash
# 1. Create a coppermind for a topic
copper forge ai-safety --topic "AI safety and alignment research"

# 2. Ingest a source file
copper store ai-safety paper.pdf        # PDF (with smart TOC-based chunking)
copper store ai-safety notes.md         # plain markdown or Obsidian note
copper store ai-safety transcript.txt   # any UTF-8 text

# 3. Ask a question
copper tap ai-safety "What are the main arguments against RLHF?"

# 4. Audit the wiki for quality
copper polish ai-safety

# 5. Start an interactive session
copper chat ai-safety
```

### Hierarchical copperminds

Nest copperminds to organise large knowledge domains into sub-topics:

```bash
# Create a parent with children
copper forge cosmere --topic "Brandon Sanderson's Cosmere"
copper forge cosmere/stormlight --topic "The Stormlight Archive"
copper forge cosmere/mistborn   --topic "The Mistborn series"

# Store a file — the Archivist routes it to the right child automatically
copper store cosmere book.pdf

# Query the tree — descends to find the best answer
copper tap cosmere "Who is Dalinar Kholin?"

# Audit the full tree
copper polish cosmere --deep       # structural reorganisation across children
copper polish cosmere --depth 2    # standard audit up to depth 2
```

Children are created at `~/.copper/minds/<parent>/children/<child>/` and are fully independent copperminds with their own raw/, wiki/, and .copper/ directories.

Copperminds are stored in `~/.copper/minds/<name>/`. Set `COPPER_MINDS_DIR` to override.

---

## CLI Reference

```
copper forge <name> [--topic TEXT]              Create a coppermind
copper forge <parent>/<child> [--topic TEXT]    Create a nested child coppermind
copper store <name> <file> [--all]              Ingest a source (or all files in raw/)
  --no-route                                    Skip LLM routing, store directly into this mind
  --into <child>                                Force routing to a specific child by name
  --flat                                        Disable PDF structure detection (store flat)
copper watch <name>                             Watch raw/ and auto-ingest (covers all descendants)
copper tap <name|a,b|--all> <question>          Query one or more copperminds
  --save                                        Save the answer to outputs/
  --with-links                                  Include linked copperminds
  --personality tap.gamemaster                  Use a named personality (see below)
copper chat <name> [--with-links]               Interactive multi-turn REPL
  [--personality NAME]                          Personality for this session
copper polish <name>                            Wiki health check
  --depth N                                     Audit up to depth N (default: full tree)
  --deep                                        Structural reorganisation: map-reduce across children
  --dry-run                                     Show the reorganisation plan without applying it
  --yes / -y                                    Apply all actions without prompting
  --fix                                         Apply LLM-proposed slug merges (asks for confirmation)
  --fix --yes                                   Apply all slug merges without prompting
copper list                                     List all copperminds (tree view)
copper status <name>                            Show stats for a coppermind
copper link <a> <b>                             Link two copperminds
copper unlink <a> <b>                           Remove a link
copper graph                                    Print the link graph
copper personalities                            List available tap personalities
copper serve [--host] [--port] [--reload]       Start the API server
```

### Tap personalities

The Archivist's voice is configurable. Built-in personalities:

| Name | Style |
|---|---|
| `tap.archivist` | Default — neutral, citation-focused |
| `tap.scholar` | Academic, comparative, in-depth |
| `tap.gamemaster` | Narrative, immersive, world-building tone |
| `tap.inquisitor` | Socratic, challenges assumptions |

Run `copper personalities` to list all registered personalities and their descriptions. Custom personalities can be added via `COPPER_USER_PROMPTS_DIR`.

---

## Supported file formats

| Format | Extension | Notes |
|---|---|---|
| Markdown | `.md` | Built-in |
| Plain text | `.txt`, `.rst`, `.html`, `.py`, … | Built-in; any UTF-8 file |
| Obsidian notes | `.md` with `[[wikilinks]]` | Auto-detected; wikilinks normalised |
| PDF | `.pdf` | Requires `pdm install -G pdf`; smart TOC-based chunking |

Any other UTF-8 readable file (`.json`, `.yaml`, `.csv`, source code, …) is accepted by default.

### PDF chunking strategy

Large PDFs are split into semantically coherent chunks before ingestion:

1. **TOC detection** — scans the first 15 pages for a table of contents (by keyword: *Index*, *Índice*, *Contents*, *Contenido*) and uses section titles as split boundaries
2. **Pattern fallback** — if no explicit header is found, detects TOC pages by density of `Title .... page` patterns
3. **LLM fallback** — if no TOC is found, asks the LLM to identify section boundaries from the document opening
4. **Naive fallback** — paragraph-aware character-based split as last resort

After all chunks are processed, a `polish` pass consolidates potential duplicates.

---

## Auto-ingest with `copper watch`

Drop files into `raw/` and let the Archivist process them automatically:

```bash
# Terminal 1 — start the watcher (covers the root mind + all descendants)
copper watch cosmere

# Terminal 2 (or Finder) — drop a file into any raw/ in the tree
cp book.pdf ~/.copper/minds/cosmere/raw/
cp chapter.md ~/.copper/minds/cosmere/children/stormlight/raw/
# → Archivist picks each file up, updates the relevant wiki, prints the result
```

Requires `pdm install -G watch`. The watcher monitors the root mind and every descendant concurrently. Files are polled for size stability before processing to handle slow copies.

---

## API Server

```bash
copper serve
# or
pdm run python -m copper.server
```

The server starts at `http://127.0.0.1:8000` by default.

- **Web UI**: `http://localhost:8000/`
- **API docs**: `http://localhost:8000/api/docs`

### Key endpoints

`{name}` accepts slash-separated paths to reach child copperminds (e.g. `cosmere/stormlight`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/minds` | List all copperminds (tree, includes children) |
| `POST` | `/minds` | Forge a coppermind |
| `GET` | `/minds/{name}` | Get stats (`is_root`, `children` list) |
| `DELETE` | `/minds/{name}` | Delete |
| `POST` | `/minds/{name}/store` | Ingest a file (multipart upload) |
| `POST` | `/minds/{name}/tap` | Ask a single question |
| `POST` | `/minds/{name}/tap/stream` | Ask with SSE streaming |
| `POST` | `/minds/{name}/chat` | Multi-turn chat (history in request body) |
| `POST` | `/minds/{name}/chat/stream` | Multi-turn chat with SSE streaming |
| `POST` | `/minds/{name}/polish` | Run a wiki audit |
| `GET` | `/minds/{name}/wiki` | List wiki pages |
| `GET` | `/minds/{name}/wiki/{slug}` | Get a page |
| `PUT` | `/minds/{name}/wiki/{slug}` | Update a page body |
| `POST` | `/minds/link` | Link two copperminds |
| `DELETE` | `/minds/link` | Unlink two copperminds |
| `GET` | `/minds/graph/all` | Full link graph |

---

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

The most common knobs:

| Variable | Default | Description |
|---|---|---|
| `COPPER_LLM_PROVIDER` | `mock` | `mock` \| `ollama` \| `anthropic` \| `openai` |
| `COPPER_LLM_MODEL` | _(empty)_ | Fallback model name |
| `COPPER_STORE_PROVIDER` / `_MODEL` | _(empty)_ | Override the LLM used for ingest + polish |
| `COPPER_TAP_PROVIDER` / `_MODEL` | _(empty)_ | Override the LLM used for tap + chat |
| `COPPER_INGEST_PROVIDER` / `_MODEL` | _(empty)_ | Vision model for multimodal PDF ingest |
| `COPPER_MINDS_DIR` | `~/.copper/minds` | Where copperminds are stored |
| `COPPER_HOST` / `COPPER_PORT` | `127.0.0.1` / `8000` | API server bind |

By default, `COPPER_LLM_PROVIDER=mock` — no real LLM calls are made.

Resolution order: **per-mind override (`.copper/config.yaml`) → workflow-level env var → generic fallback → provider default**.

### Using different models for store vs. tap

Store benefits from a more capable model; tap can use a faster local one:

```bash
# .env — index with Claude, query with Ollama
COPPER_STORE_PROVIDER=anthropic
COPPER_STORE_MODEL=claude-opus-4-7
COPPER_ANTHROPIC_API_KEY=sk-ant-...

COPPER_TAP_PROVIDER=ollama
COPPER_TAP_MODEL=llama3.2
COPPER_OLLAMA_BASE_URL=http://localhost:11434
```

### Overriding models per coppermind

Edit `<mind>/.copper/config.yaml`:

```yaml
store_provider: anthropic
store_model: claude-sonnet-4-6
tap_provider: ollama
tap_model: gemma3:4b
tap_personality: tap.gamemaster
```

Only set the fields you want to override — absent fields inherit from the global config.

### Full reference

See [`docs/configuration.md`](docs/configuration.md) for every env var, including PDF ingest tuning, custom-prompt directories, retrieval ceilings, and complete recipes (all-local, hybrid, Docker).

---

## Docker

Copper ships with a `Dockerfile`. Ollama is expected to run externally (on the host or another service).

```bash
# Build
docker build -t copper:dev .

# Run (Ollama on host)
docker run -d --name copper \
  -p 8000:8000 \
  -v ~/.copper/minds:/data/minds \
  --env-file .env \
  -e COPPER_MINDS_DIR=/data/minds \
  -e COPPER_HOST=0.0.0.0 \
  -e COPPER_OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  copper:dev
```

Copperminds are stored in a host-mounted volume so they persist across container restarts.

---

## Coppermind folder layout

```
~/.copper/minds/<name>/
├── raw/                  # Immutable source files (never modified by the Archivist)
├── wiki/                 # LLM-maintained wiki
│   ├── index.md          # Table of contents (also _meta.md for child nodes)
│   ├── log.md            # Change log
│   ├── lint-report-*.md  # Polish audit reports
│   └── *.md              # Knowledge pages
├── outputs/              # Saved tap answers
├── children/             # Nested child copperminds (each is a full coppermind)
│   └── <child>/          # Same structure recursively
└── .copper/
    ├── config.yaml       # Name, topic, linked minds, model override
    └── schema.md         # Archivist instructions (auto-generated, freely editable)
```

The `schema.md` file is the most powerful customisation point. Edit it to change how the Archivist organises knowledge, what taxonomies to use, which fields to track, and how pages should be structured.

---

## Development

```bash
# Run tests (all use MockLLM — no real LLM calls)
pdm run pytest -v

# Lint / format / type check
pdm run ruff check src
pdm run black src
pdm run mypy src
```

---

## Architecture

```
src/copper/
├── core/
│   ├── coppermind.py     # CopperMind: forge, get, link, stats
│   └── wiki.py           # WikiManager: page CRUD, frontmatter, index
├── ingest/
│   ├── base.py           # IngestPlugin abstract base + naive_split utility
│   ├── plain.py          # PlainTextPlugin: .md, .txt, any UTF-8
│   ├── obsidian.py       # ObsidianPlugin: normalises [[wikilinks]]
│   ├── pdf.py            # PDFPlugin: pdfplumber + hybrid TOC/LLM chunking
│   ├── image_describer.py# Vision-model descriptions for diagrams in PDFs
│   └── registry.py       # IngestRegistry: ordered plugin dispatch
├── llm/
│   ├── base.py           # LLMBase abstract interface
│   ├── mock.py           # MockLLM for tests
│   └── bridge_adapter.py # Adapter for core-llm-bridge (Ollama, Anthropic, OpenAI)
├── prompts/
│   ├── __init__.py       # render_prompt() / list_prompts() — YAML prompt registry
│   └── *.yaml            # Built-in prompts (tap personalities, store archivist, etc.)
├── retrieval/
│   ├── base.py           # Retriever protocol + RetrievalResult
│   ├── llm.py            # LLMRetriever: LLM picks relevant pages from the index
│   ├── keyword.py        # KeywordRetriever: keyword match, no LLM cost
│   ├── alloy.py          # AlloyRetriever: fuses multiple retriever results
│   └── factory.py        # build_default_retriever() — wires the pipeline from Settings
├── workflows/
│   ├── store.py          # Source → chunks → LLM router → wiki pages (+ auto-polish)
│   ├── tap.py            # Question → two-stage retrieval → LLM → answer (recursive for trees)
│   ├── polish.py         # Wiki audit → lint report (recursive for trees)
│   └── deep_polish.py    # Map-reduce reorganisation: entity extraction → plan → apply moves
├── api/
│   ├── app.py            # FastAPI factory
│   ├── routes/           # minds, workflows
│   ├── deps.py           # Dependency injection (LLM provider wiring)
│   └── templates/        # Jinja2 + HTMX UI
├── watch.py              # Watchdog-based auto-ingest
├── cli.py                # typer + rich CLI
├── config.py             # pydantic-settings (single source of truth for all config)
└── server.py             # uvicorn entry point
```

LLM integration is decoupled behind `LLMBase`. The real provider is an optional dependency — the core system works entirely with `MockLLM` out of the box.

**Tap retrieval** works in two stages: first an LLM scan of the wiki index identifies the most relevant pages, then a keyword pass augments the selection. The two results are fused before the answering call — avoiding the "full wiki in context" problem at scale.
