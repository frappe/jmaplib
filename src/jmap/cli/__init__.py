"""The ``jmap`` command: a command-line JMAP client built on this library.

Installed by ``pip install 'jmaplib[cli]'``. This module imports nothing from the
extra, so ``import jmap.cli`` works without it and :func:`main` can say what is
missing instead of failing with a traceback.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The extra's packages the command cannot start without. The others are
#: imported lazily and each has a fallback.
REQUIRED: Final = ("typer", "rich")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``jmap`` command and return its exit status."""
    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        sys.stderr.write(
            f"the jmap command needs {', '.join(missing)}: pip install 'jmaplib[cli]'\n"
        )
        return 2
    from jmap.cli.app import run

    return run(argv)
