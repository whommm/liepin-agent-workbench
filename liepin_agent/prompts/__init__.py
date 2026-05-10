"""Centralized prompt management for the Agent brain.

Prompts are stored as Markdown files so they can be versioned and iterated
without touching Python code.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Dict


_PROMPT_CACHE: Dict[str, str] = {}


def get_prompt(name: str) -> str:
    """Load a prompt by name from the prompts package.

    Name should be the stem of a .md file under this package, e.g.
    ``get_prompt("job_understanding")`` loads ``job_understanding.md``.
    """
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]

    # Try to read from package resources first, then fallback to filesystem
    try:
        text = importlib.resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        # Fallback for development / editable installs
        pkg_dir = Path(__file__).parent
        path = pkg_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt '{name}.md' not found in {pkg_dir}")
        text = path.read_text(encoding="utf-8")

    _PROMPT_CACHE[name] = text
    return text


def clear_cache() -> None:
    """Clear the in-memory prompt cache so subsequent calls reload from disk."""
    _PROMPT_CACHE.clear()
