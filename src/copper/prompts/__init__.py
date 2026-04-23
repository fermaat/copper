"""
Prompt management for Copper.

Wraps core-llm-bridge's :class:`PromptManager` so we load all prompts from YAML
files instead of hardcoding them in .py modules. This enables:

- Clean separation of content (YAML) from logic (Python).
- Swappable personalities for the tap workflow (archivist, gamemaster, scholar…).
- User-defined prompts via ``COPPER_USER_PROMPTS_DIR`` that override built-ins
  by matching the prompt name.

Usage:
    from copper.prompts import render_prompt, list_prompts

    system = render_prompt("tap.archivist")
    personalities = list_prompts(prefix="tap.")

Prompt files live under ``src/copper/prompts/`` (shipped with the package) and,
optionally, a user-configured directory. The YAML schema follows core-llm-bridge:

    name: tap.archivist
    description: Default voice for tap — neutral, citation-focused.
    template: |
      You are the Archivist...
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings

if TYPE_CHECKING:
    from core_llm_bridge.utils.prompt_manager import PromptManager


# Module-level cache — the PromptManager is built once on first use.
_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """Return the singleton PromptManager, building it on first call."""
    global _manager
    if _manager is None:
        _manager = _build_manager()
    return _manager


def render_prompt(name: str, **variables: object) -> str:
    """Render a registered prompt by name, substituting ``$variables`` if any.

    Raises ``ValueError`` if the prompt name is unknown (prevents silent
    fall-through to empty strings that would hollow out the LLM's behaviour).
    """
    manager = get_prompt_manager()
    template = manager.get(name)
    if template is None:
        raise ValueError(
            f"Prompt '{name}' not registered. "
            f"Available prompts: {sorted(manager.list_templates())}"
        )
    if variables:
        # core-llm-bridge has no stubs, so the render call is typed Any.
        return str(template.render(**variables))
    # No variables — just the raw template string.
    return str(template.template_str)


def list_prompts(prefix: str | None = None) -> list[str]:
    """List registered prompt names, optionally filtered by prefix (e.g. 'tap.')."""
    names = get_prompt_manager().list_templates()
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return sorted(names)


def _build_manager() -> PromptManager:
    """Instantiate the PromptManager and load built-in + user prompts.

    Priority when names collide: user prompts override built-ins.
    """
    from core_llm_bridge.utils.prompt_manager import PromptManager

    manager = PromptManager()

    # 1. Built-in prompts shipped with the package — same directory as this module.
    builtin_dir = Path(__file__).parent
    _load_yaml_files(manager, builtin_dir, source_label="built-in")

    # 2. Optional user prompts directory — overrides built-ins by name.
    user_dir_str = getattr(settings, "copper_user_prompts_dir", "")
    if user_dir_str:
        user_dir = Path(user_dir_str).expanduser()
        if user_dir.exists():
            _load_yaml_files(manager, user_dir, source_label="user", allow_override=True)
        else:
            logger.warning(
                f"[prompts] COPPER_USER_PROMPTS_DIR is set but does not exist: {user_dir}"
            )

    logger.info(f"[prompts] Loaded {len(manager.list_templates())} prompts.")
    return manager


def _load_yaml_files(
    manager: PromptManager,
    directory: Path,
    source_label: str,
    allow_override: bool = False,
) -> None:
    """Load every ``*.yaml`` file in *directory* into *manager*.

    ``allow_override`` lets user prompts replace built-ins with the same name.
    """
    import yaml

    for yaml_file in sorted(directory.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f) or {}
            name = data.get("name")
            if not name:
                logger.warning(f"[prompts] Skipping {yaml_file}: missing 'name' field.")
                continue
            if manager.get(name) is not None:
                if allow_override:
                    manager.unregister(name)
                    logger.info(f"[prompts] Overriding '{name}' with {source_label} version.")
                else:
                    logger.warning(f"[prompts] '{name}' already registered; skipping {yaml_file}.")
                    continue
            manager.load_from_yaml(yaml_file)
        except (ValueError, KeyError, OSError) as exc:
            logger.warning(f"[prompts] Failed to load {yaml_file}: {exc}")


def reset_manager() -> None:
    """Reset the singleton — useful in tests that tweak settings at runtime."""
    global _manager
    _manager = None
