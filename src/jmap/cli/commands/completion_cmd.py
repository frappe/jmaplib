"""``jmap completion``: Tab completion for your shell.

Typer's ``--install-completion`` finds the shell by looking at the process
that started the command, which fails under some terminals and in scripts.
This names the shell instead, and completes under whichever name was run:
``jmap``, or ``jmap-cli`` where macOS has its own ``/usr/bin/jmap``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

# typer.completion uses these without re-exporting them; they are where Typer keeps them.
from typer._completion_shared import get_completion_script, install

from jmap.cli.commands import cli


class Shell(StrEnum):
    bash = "bash"
    zsh = "zsh"
    fish = "fish"
    powershell = "powershell"
    pwsh = "pwsh"


def completion(
    ctx: typer.Context,
    shell: Annotated[Shell, typer.Argument(help="Your shell.")],
    install_it: Annotated[
        bool, typer.Option("--install", help="Add it to the shell's startup file.")
    ] = False,
) -> None:
    """Print the Tab completion script for your shell, or --install it.

    For zsh, add eval "$(jmap completion zsh)" to ~/.zshrc; for bash, the same
    in ~/.bashrc; for fish, save it as ~/.config/fish/completions/jmap.fish.
    --install does that for you. Open a new shell afterwards.
    """
    context = cli(ctx)
    program = ctx.find_root().info_name or "jmap"
    variable = "_{}_COMPLETE".format(program.replace("-", "_").upper())
    if install_it:
        _, path = install(shell=shell.value, prog_name=program, complete_var=variable)
        context.renderer.note(f"added {shell.value} completion to {path}; open a new shell")
        return
    script = get_completion_script(prog_name=program, complete_var=variable, shell=shell.value)
    context.out.file.write(script.rstrip("\n") + "\n")
