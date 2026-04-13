# Copper

AI-maintained knowledge bases, inspired by Karpathy's wiki concept and the Cosmere's *copperminds* — repositories of pure knowledge, maintained by a Feruchemical Archivist.

You feed Copper raw sources (articles, notes, transcripts, docs). The Archivist (an LLM) reads each source and compiles it into a structured markdown wiki. No embeddings, no RAG, no vector databases — just folders and text files you can read, edit, and version-control yourself.

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

# Optional: install the LLM bridge (needed for Ollama / real providers)
pdm install -G llm
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

# 2. Ingest a source file (copied to raw/ and processed by the Archivist)
copper store ai-safety paper.pdf

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
| `COPPER_LLM_PROVIDER` | `mock` | LLM provider (`mock`, `ollama`) |
| `COPPER_LLM_MODEL` | _(empty)_ | Model name (e.g. `llama3.2`) |
| `COPPER_MINDS_DIR` | `~/.copper/minds` | Where copperminds are stored |
| `COPPER_HOST` | `127.0.0.1` | API server host |
| `COPPER_PORT` | `8000` | API server port |
| `COPPER_RELOAD` | `false` | Hot-reload for development |
| `LOG_LEVEL` | `INFO` | Logging level |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |

By default, `COPPER_LLM_PROVIDER=mock` — no real LLM calls are made. Responses are auto-generated placeholders, useful for exploring the structure without a running model.

To use Ollama:

```bash
COPPER_LLM_PROVIDER=ollama
COPPER_LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434
```

---

## Docker

Copper ships with a `Dockerfile` and `docker-compose.yml`. Ollama is expected to run externally (on the host or another service).

```bash
# Build and start
docker compose up --build

# Ollama on host Mac/Windows — host.docker.internal is set automatically
# Override if Ollama is running elsewhere:
OLLAMA_BASE_URL=http://my-ollama-host:11434 docker compose up
```

Copperminds are stored in a named volume (`copper-minds`) so they persist across container restarts.

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
# Run tests
pdm run pytest -v

# Lint
pdm run ruff check src

# Format
pdm run black src

# Type check
pdm run mypy src

# Clean caches
make clean
```

All tests use `MockLLM` — no real LLM calls are made in the test suite.

---

## Architecture

```
src/copper/
├── core/
│   ├── coppermind.py     # CopperMind: forge, get, link, stats
│   └── wiki.py           # WikiManager: page CRUD, frontmatter, index
├── llm/
│   ├── base.py           # LLMBase abstract interface
│   ├── mock.py           # MockLLM for tests
│   └── bridge_adapter.py # Adapter for core-llm-bridge (Ollama etc.)
├── workflows/
│   ├── store.py          # Source → LLM → wiki pages
│   ├── tap.py            # Question → LLM → answer (multi-mind aware)
│   └── polish.py         # Wiki audit → lint report
├── api/
│   ├── app.py            # FastAPI factory
│   ├── routes/           # minds, workflows
│   ├── deps.py           # Dependency injection
│   └── templates/        # Jinja2 + HTMX UI
├── cli.py                # typer + rich CLI
├── config.py             # pydantic-settings configuration
└── server.py             # uvicorn entry point
```

LLM integration is decoupled behind `LLMBase`. The real provider (Ollama via `core-llm-bridge`) is an optional dependency — the core system works entirely with `MockLLM` out of the box.
