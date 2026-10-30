"""``jmap shell``: an interactive session on one connection."""

from __future__ import annotations

import typer

from jmap.cli.commands import cli
from jmap.cli.paths import state_dir
from jmap.cli.shell import Shell, line_source


def shell(ctx: typer.Context) -> None:
    """Run commands interactively on one connection: cd, ls, read 2, exit.

    Global options given here (-a, --no-keyring...) apply to every line.
    """
    context = cli(ctx)
    session = Shell(context, context.flags)
    history = state_dir(context.environ) / "history"
    lines = context.lines or line_source(context, history, session.candidates)
    raise typer.Exit(session.run(lines))
