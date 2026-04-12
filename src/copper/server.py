"""
Entry point for running the Copper API server directly.

    python -m copper.server
    # or via CLI:
    copper serve
"""

from __future__ import annotations

import uvicorn

from copper.api.app import create_app
from copper.config import settings


def main() -> None:
    app = create_app()
    uvicorn.run(
        app,
        host=settings.copper_host,
        port=settings.copper_port,
        reload=settings.copper_reload,
    )


if __name__ == "__main__":
    main()
