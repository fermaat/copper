# Copper API — multi-stage build
# Ollama runs as an EXTERNAL service; configure OLLAMA_BASE_URL in .env

FROM python:3.12-slim AS base
WORKDIR /app

# git is required for dependencies installed from GitHub (core-utils, core-llm-bridge)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pdm

# ------------------------------------------------------------------ #
# Dependencies stage                                                  #
# ------------------------------------------------------------------ #
FROM base AS deps

COPY pyproject.toml pdm.lock* ./

# Install main runtime deps from lockfile
RUN pdm install --no-self --no-editable

# PDM venvs don't ship with pip — bootstrap it, then install optional extras
RUN /app/.venv/bin/python -m ensurepip --upgrade && \
    /app/.venv/bin/python -m pip install --no-cache-dir \
    "core-llm-bridge @ git+https://github.com/fermaat/core-llm-bridge.git" \
    pdfplumber

# ------------------------------------------------------------------ #
# Application stage                                                   #
# ------------------------------------------------------------------ #
FROM base AS app

COPY --from=deps /app/.venv /app/.venv
COPY src/ ./src/
COPY pyproject.toml ./

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Minds directory — mount a volume here to persist copperminds
ENV COPPER_MINDS_DIR=/data/minds
RUN mkdir -p /data/minds

EXPOSE 8000

CMD ["python", "-m", "copper.server"]
