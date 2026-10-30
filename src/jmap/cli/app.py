"""The command tree's root: global options, and the one place errors are caught.

:func:`run` is the only ``try``. Commands raise; it reports the error once, picks
the exit code, and always closes the connection. The same tree runs one-shot
from the shell and line by line inside ``jmap shell``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import typer

from jmap.__about__ import __version__
from jmap.cli.commands import register
from jmap.cli.completers import accounts
from jmap.cli.context import CLIContext, GlobalFlags
from jmap.cli.errors import ExitCode, report
from jmap.cli.output import ColorMode, OutputFormat

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Global options that take a value, which ``run`` moves with the option.
VALUE_OPTIONS: Final = frozenset(
    {"-a", "--account", "--account-id", "-o", "--output", "--color", "--config"}
)
#: Global options that stand alone.
FLAG_OPTIONS: Final = frozenset(
    {
        "--json",
        "--jsonl",
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "--no-keyring",
        "--experimental",
        "--insecure",
        "-y",
        "--yes",
    }
)
#: No command may define an option with one of these names.
RESERVED: Final = VALUE_OPTIONS | FLAG_OPTIONS
#: The names the command is installed under (``pyproject.toml``'s scripts).
PROGRAMS: Final = frozenset({"jmap", "jmap-cli"})

_VERBOSE: Final = re.compile(r"-v+")

app: typer.Typer = typer.Typer(
    name="jmap",
    help="A command-line JMAP client: mail, contacts, calendars and files.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich",
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"jmap {__version__}")
        raise typer.Exit


@app.callback()
def root(
    ctx: typer.Context,
    account: Annotated[
        str | None,
        typer.Option("-a", "--account", help="Named account to use.", autocompletion=accounts),
    ] = None,
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="JMAP account inside the session, by id or name."),
    ] = None,
    output: Annotated[
        OutputFormat | None, typer.Option("-o", "--output", help="Output format.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Same as -o json.")] = False,
    jsonl_output: Annotated[bool, typer.Option("--jsonl", help="Same as -o jsonl.")] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="No notes on stderr.")] = False,
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Trace requests; -vv for more.")
    ] = 0,
    color: Annotated[ColorMode | None, typer.Option("--color", help="When to use colour.")] = None,
    no_keyring: Annotated[
        bool, typer.Option("--no-keyring", help="Neither read nor store secrets in the keyring.")
    ] = False,
    experimental: Annotated[
        bool, typer.Option("--experimental", help="Resolve draft-tracking capabilities.")
    ] = False,
    insecure: Annotated[
        bool, typer.Option("--insecure", help="Do not verify TLS certificates.")
    ] = False,
    config: Annotated[str | None, typer.Option("--config", help="Configuration file.")] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Answer yes to confirmations.")] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    if not isinstance(ctx.obj, CLIContext):
        ctx.obj = CLIContext.from_environment(os.environ)
        ctx.call_on_close(ctx.obj.close)
    if json_output:
        output = OutputFormat.JSON
    elif jsonl_output:
        output = OutputFormat.JSONL
    ctx.obj.configure(
        GlobalFlags(
            account=account,
            account_id=account_id,
            output=output,
            quiet=quiet,
            verbose=verbose,
            color=color,
            no_keyring=no_keyring,
            experimental=experimental,
            insecure=insecure,
            config=config,
            yes=yes,
        )
    )


def run(
    argv: Sequence[str] | None = None,
    *,
    context: CLIContext | None = None,
    close: bool = True,
) -> int:
    """Run one command line and return its exit status.

    Typer runs standalone: it reports usage errors itself and ends with
    ``SystemExit``, which becomes the return value here so ``jmap shell`` can
    keep going. Anything else a command raises is reported by :func:`report`.
    The shell passes ``close=False`` to keep its connection between lines.
    """
    context = context or CLIContext.from_environment(os.environ)
    args = hoist_globals(sys.argv[1:] if argv is None else argv)
    command = typer.main.get_command(app)
    status: object = ExitCode.OK
    program = _program() if argv is None else "jmap"
    try:
        command.main(args=args, prog_name=program, standalone_mode=True, obj=context)
    except SystemExit as exit_:
        status = exit_.code
    except Exception as error:
        status = report(
            error,
            context.err,
            json_mode=context.options.json_mode,
            verbose=context.flags.verbose,
        )
    finally:
        if close:
            context.close()
    return _status(status)


def _program() -> str:
    """The name the command was run as, which Tab completion is registered under."""
    name = Path(sys.argv[0]).stem
    return name if name in PROGRAMS else "jmap"


def hoist_globals(argv: Sequence[str]) -> list[str]:
    """Move global options to the front, so ``jmap mail list --json`` works.

    Click only accepts an option on the command that defines it. Everything
    after ``--`` is left where it is.
    """
    front: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            rest += argv[index:]
            break
        if _is_global(arg):
            front.append(arg)
        elif arg in VALUE_OPTIONS and index + 1 < len(argv):
            front += argv[index : index + 2]
            index += 1
        else:
            rest.append(arg)
        index += 1
    return front + rest


def _is_global(arg: str) -> bool:
    """A global flag, or a global ``--option=value``."""
    if arg in FLAG_OPTIONS or _VERBOSE.fullmatch(arg):
        return True
    name, equals, _ = arg.partition("=")
    return bool(equals) and name.startswith("--") and name in VALUE_OPTIONS


def _status(code: object) -> int:
    if code is None:
        return ExitCode.OK
    return code if isinstance(code, int) else ExitCode.FAILURE


# Last, so a command module may import this one (the shell runs `run`).
register(app)
