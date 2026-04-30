"""
Watchdog auto-ingest — watch raw/ and store new files automatically.

Usage:
    copper watch <name>
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from copper.core.coppermind import CopperMind
from copper.llm.base import LLMBase
from copper.workflows.store import StoreResult, StoreWorkflow

# Files that land in raw/ but should not be auto-ingested
_IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload", ".download"}


def _wait_for_stable(path: Path, timeout: float = 30.0, poll: float = 0.5) -> bool:
    """Wait until the file size stops changing (i.e., the copy/write is complete).

    Returns True when stable, False if the timeout is reached.
    """
    prev_size = -1
    elapsed = 0.0
    while elapsed < timeout:
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(poll)
            elapsed += poll
            continue
        if size == prev_size and size > 0:
            return True
        prev_size = size
        time.sleep(poll)
        elapsed += poll
    return False


class _RawDirHandler:
    """Handles filesystem events on raw/ and triggers StoreWorkflow."""

    def __init__(
        self,
        mind: CopperMind,
        llm: LLMBase,
        on_result: Callable[[Path, StoreResult], None] | None = None,
        on_error: Callable[[Path, Exception], None] | None = None,
    ) -> None:
        self.mind = mind
        self.llm = llm
        self.on_result = on_result
        self.on_error = on_error

    def _should_process(self, path: Path) -> bool:
        if path.name in _IGNORED_NAMES:
            return False
        if path.suffix.lower() in _IGNORED_SUFFIXES:
            return False
        return True

    def process(self, path: Path) -> None:
        """Called when a new file appears. Blocks until the workflow finishes."""
        if not self._should_process(path):
            return

        if not _wait_for_stable(path):
            err = TimeoutError(f"File '{path.name}' did not stabilize in time — skipping.")
            if self.on_error:
                self.on_error(path, err)
            return

        workflow = StoreWorkflow(self.mind, self.llm)
        try:
            result = workflow.run(path)
            if self.on_result:
                self.on_result(path, result)
        except Exception as exc:  # noqa: BLE001
            if self.on_error:
                self.on_error(path, exc)


def watch_raw_dir(
    mind: CopperMind,
    llm: LLMBase,
    *,
    on_result: Callable[[Path, StoreResult], None] | None = None,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> None:
    """Block, watching *mind.raw_dir* for new files.

    Calls *on_result* when a file is successfully ingested and *on_error*
    when something goes wrong. Exits cleanly on KeyboardInterrupt.

    Requires the optional 'watch' extra:
        pdm install -G watch
    """
    try:
        from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        raise ImportError(
            "watchdog is required for auto-ingest.\n" "Install it with: pdm install -G watch"
        )

    handler_obj = _RawDirHandler(mind, llm, on_result=on_result, on_error=on_error)

    class _WatchdogBridge(FileSystemEventHandler):
        def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
            if not event.is_directory:
                handler_obj.process(Path(str(event.src_path)))

        def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
            # Handles files moved/renamed into raw/ (e.g., drag-and-drop on some OSes)
            if not event.is_directory:
                handler_obj.process(Path(str(event.dest_path)))

    observer = Observer()
    observer.schedule(_WatchdogBridge(), str(mind.raw_dir), recursive=False)
    observer.start()

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
