"""Where the command keeps its files.

The XDG variables are honoured on every platform, Windows included, so a test or
a user can move everything with one variable. Without them the defaults are the
XDG ones, or ``%APPDATA%`` and ``%LOCALAPPDATA%`` on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

APP: Final = "jmap"


def config_path(environ: Mapping[str, str], *, os_name: str = os.name) -> Path:
    """The configuration file: ``$JMAP_CONFIG``, else ``config.toml`` in the config dir."""
    explicit = environ.get("JMAP_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return _base(environ, "XDG_CONFIG_HOME", "APPDATA", ".config", os_name) / APP / "config.toml"


def state_dir(environ: Mapping[str, str], *, os_name: str = os.name) -> Path:
    """Shell history and the result cursor: state worth keeping, not worth backing up."""
    base = _base(environ, "XDG_STATE_HOME", "LOCALAPPDATA", ".local/state", os_name)
    return base / APP


def _base(environ: Mapping[str, str], xdg: str, windows: str, fallback: str, os_name: str) -> Path:
    if environ.get(xdg):
        return Path(environ[xdg])
    if os_name == "nt" and environ.get(windows):
        return Path(environ[windows])
    return _home(environ) / fallback


def _home(environ: Mapping[str, str]) -> Path:
    home = environ.get("HOME") or environ.get("USERPROFILE")
    return Path(home) if home else Path.home()
