# Copper

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

Copperminds are stored in `~/.copper/minds/<name>/`. Set `COPPER_MINDS_DIR` to override.

---

## CLI Reference

```
copper forge <name> [--topic TEXT]          Create a coppermind
copper store <name> <file> [--all]          Ingest a source (or all files in raw/)
copper watch <name>                         Watch raw/ and auto-ingest new files
copper tap <name|a,b|--all> <question>      Query one or more copperminds
  --save                                    Save the answer to outputs/
  --with-links                              Include linked copperminds
copper chat <name> [--with-links]           Interactive REPL
copper polish <name>                        Wiki health check
copper list                                 List all copperminds
copper status <name>                        Show stats for a coppermind
copper link <a> <b>                         Link two copperminds
copper unlink <a> <b>                       Remove a link
copper graph                                Print the link graph
copper serve [--host] [--port] [--reload]   Start the API server
```

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
# Terminal 1 — start the watcher
copper watch ai-safety

# Terminal 2 (or Finder) — drop a file into raw/
cp paper.pdf ~/.copper/minds/ai-safety/raw/
# → Archivist picks it up, updates the wiki, prints the result
```

Requires `pdm install -G watch`. The watcher polls for file-size stability before processing large files.

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

| Method | Path | Description |
|---|---|---|
| `GET` | `/minds` | List all copperminds |
| `POST` | `/minds` | Forge a coppermind |
| `GET` | `/minds/{name}` | Get stats |
| `DELETE` | `/minds/{name}` | Delete |
| `POST` | `/minds/{name}/store` | Ingest a file (multipart upload) |
| `POST` | `/minds/{name}/tap` | Ask a question |
| `POST` | `/minds/{name}/tap/stream` | Ask with SSE streaming |
| `POST` | `/minds/{name}/polish` | Run a wiki audit |
| `GET` | `/minds/{name}/wiki` | List wiki pages |
| `GET` | `/minds/{name}/wiki/{slug}` | Get a page |
| `POST` | `/minds/link` | Link two copperminds |
| `DELETE` | `/minds/link` | Unlink two copperminds |
| `GET` | `/minds/graph/all` | Full link graph |

---

## Configuration

Copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `COPPER_LLM_PROVIDER` | `mock` | Fallback provider: `mock`, `ollama`, `anthropic`, `openai` |
| `COPPER_LLM_MODEL` | _(empty)_ | Fallback model name |
| `COPPER_STORE_PROVIDER` | _(empty)_ | Provider for store + polish (overrides fallback) |
| `COPPER_STORE_MODEL` | _(empty)_ | Model for store + polish |
| `COPPER_TAP_PROVIDER` | _(empty)_ | Provider for tap + chat (overrides fallback) |
| `COPPER_TAP_MODEL` | _(empty)_ | Model for tap + chat |
| `COPPER_MINDS_DIR` | `~/.copper/minds` | Where copperminds are stored |
| `COPPER_HOST` | `127.0.0.1` | API server host |
| `COPPER_PORT` | `8000` | API server port |
| `COPPER_RELOAD` | `false` | Hot-reload for development |
| `LOG_LEVEL` | `INFO` | Logging level |
| `COPPER_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `COPPER_OLLAMA_TIMEOUT` | `300` | Ollama request timeout (seconds) |
| `COPPER_ANTHROPIC_API_KEY` | _(empty)_ | Anthropic API key |
| `COPPER_OPENAI_API_KEY` | _(empty)_ | OpenAI API key |

By default, `COPPER_LLM_PROVIDER=mock` — no real LLM calls are made.

### Using different models for store vs. tap

Store (ingestion) benefits from a more capable model; tap (queries) can use a faster local one. Configure them independently:

```bash
# .env — index with Claude, query with Ollama
COPPER_STORE_PROVIDER=anthropic
COPPER_STORE_MODEL=claude-opus-4-6
COPPER_ANTHROPIC_API_KEY=sk-ant-...

COPPER_TAP_PROVIDER=ollama
COPPER_TAP_MODEL=llama3.2
COPPER_OLLAMA_BASE_URL=http://localhost:11434
```

Resolution order for each workflow: **per-mind override → workflow-level env var → generic fallback**.

### Overriding models per coppermind

Edit `.copper/config.yaml` inside the coppermind folder:

```yaml
# ~/.copper/minds/my-mind/.copper/config.yaml
store_provider: anthropic
store_model: claude-sonnet-4-6
tap_provider: ollama
tap_model: gemma3:4b
```

Only set the fields you want to override — absent fields inherit from the global config.

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
│   ├── index.md          # Table of contents
│   ├── log.md            # Change log
│   ├── lint-report-*.md  # Polish audit reports
│   └── *.md              # Knowledge pages
├── outputs/              # Saved tap answers
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
│   └── registry.py       # IngestRegistry: ordered plugin dispatch
├── llm/
│   ├── base.py           # LLMBase abstract interface
│   ├── mock.py           # MockLLM for tests
│   └── bridge_adapter.py # Adapter for core-llm-bridge (Ollama, Anthropic, OpenAI)
├── workflows/
│   ├── store.py          # Source → chunks → LLM → wiki pages (+ auto-polish)
│   ├── tap.py            # Question → wiki context → LLM → answer
│   └── polish.py         # Wiki audit → lint report
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
