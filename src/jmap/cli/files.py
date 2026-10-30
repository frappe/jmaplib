"""Writing downloaded files: safe names, no silent overwrites."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from jmap.cli.errors import UsageError

if TYPE_CHECKING:
    from pathlib import Path

#: Characters no file name should carry from a message: separators, controls,
#: and the ones Windows refuses.
_UNSAFE: Final = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def safe_name(name: str | None, fallback: str) -> str:
    """``name`` as a single, harmless file name; ``fallback`` when nothing is left."""
    cleaned = _UNSAFE.sub("_", name or "").strip(" .")
    return cleaned or fallback


def target_path(directory: Path, name: str, *, force: bool) -> Path:
    """Where to write ``name`` in ``directory``: ``a (1).pdf`` rather than over ``a.pdf``."""
    path = directory / name
    if force or not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    number = 1
    while (candidate := directory / f"{stem} ({number}){suffix}").exists():
        number += 1
    return candidate


def write_file(path: Path, data: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise UsageError(f"{path} exists", hint="pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
