"""Editing text in the person's own editor.

The text goes to a private temporary file, the editor runs on it, and what is
saved comes back. ``$VISUAL`` wins over ``$EDITOR``, and ``defaults.editor`` in
the configuration wins over both.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from jmap.cli.errors import CLIError

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext


def editor_command(context: CLIContext) -> str:
    """The editor to run, as a shell-style command line."""
    try:
        configured = context.config.defaults.editor
    except CLIError:
        configured = ""  # a broken file is what the editor may be opened to fix
    fallback = "notepad" if os.name == "nt" else "vi"
    return configured or context.environ.get("VISUAL") or context.environ.get("EDITOR") or fallback


def run_editor(command: str, target: Path) -> None:
    done = subprocess.run([*shlex.split(command), str(target)], check=False)
    if done.returncode != 0:
        raise CLIError(f"the editor exited with status {done.returncode}")


def edit_text(command: str, text: str, *, suffix: str = ".txt") -> str:
    """``text`` after the person has edited it."""
    with tempfile.TemporaryDirectory(prefix="jmap-") as directory:
        target = Path(directory) / f"message{suffix}"
        target.write_text(text, encoding="utf-8")
        run_editor(command, target)
        return target.read_text(encoding="utf-8")
