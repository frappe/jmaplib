"""``jmap api``: call any modelled method with raw JSON arguments.

The library's gates still apply: a method this build does not model, or one the
server does not advertise, is refused before anything is sent. ``accountId`` is
filled in from the selected JMAP account unless the arguments give one.
"""

from __future__ import annotations

from typing import Annotated

import typer

from jmap.cli.commands import cli, json_argument

app: typer.Typer = typer.Typer(no_args_is_help=True)


@app.command()
def call(
    ctx: typer.Context,
    method: Annotated[str, typer.Argument(help="Method name, such as Mailbox/get.")],
    arguments: Annotated[
        str | None, typer.Argument(help="JSON object of arguments, @FILE, or - for stdin.")
    ] = None,
    using: Annotated[
        list[str] | None, typer.Option(help="Extra capability URN for 'using' (repeatable).")
    ] = None,
) -> None:
    """Make one method call and print its response arguments as JSON."""
    context = cli(ctx)
    args = json_argument(arguments, context)
    client = context.require(method)
    with client.batch(extra_using=frozenset(using or ())) as batch:
        handle = batch.add(method, args)
    context.renderer.json(handle.result)
