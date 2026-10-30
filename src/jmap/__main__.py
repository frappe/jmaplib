"""``python -m jmap``: the same command as the ``jmap`` script."""

from __future__ import annotations

from jmap.cli import main

if __name__ == "__main__":  # pragma: no cover - the interpreter's entry
    raise SystemExit(main())
