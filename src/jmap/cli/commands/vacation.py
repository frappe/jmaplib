"""``jmap vacation``: the automatic reply while you are away."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.context import CLIContext
from jmap.cli.errors import UsageError
from jmap.cli.output import Column, View, wire_of

app: typer.Typer = typer.Typer(no_args_is_help=True)

_SINGLETON = "singleton"


@app.command()
def show(ctx: typer.Context) -> None:
    """Show the vacation response."""
    context = cli(ctx)
    client = context.require("VacationResponse/get")
    with client.batch() as batch:
        handle = batch.add("VacationResponse/get", {"ids": [_SINGLETON]})
    found: list[dict[str, Any]] = wire_of(handle.result)["list"]
    item = found[0] if found else {}
    columns = [
        Column("Enabled", "isEnabled", lambda i: "yes" if i.get("isEnabled") else "no"),
        Column("From", "fromDate"),
        Column("Until", "toDate"),
        Column("Subject", "subject"),
        Column("Body", "textBody"),
    ]
    context.renderer.render(View([item], columns, single=True))


@app.command("set")
def set_(
    ctx: typer.Context,
    subject: Annotated[str | None, typer.Option(help="The reply's subject.")] = None,
    body: Annotated[str | None, typer.Option(help="The reply's text.")] = None,
    body_file: Annotated[str | None, typer.Option(help="Read the text from a file.")] = None,
    html: Annotated[str | None, typer.Option(help="An HTML version of the text.")] = None,
    start: Annotated[
        str | None, typer.Option("--from", help='Start: 2026-12-20T00:00:00Z; "" clears it.')
    ] = None,
    until: Annotated[
        str | None, typer.Option("--until", help='End, in the same form; "" clears it.')
    ] = None,
    enabled: Annotated[
        bool | None, typer.Option("--enable/--disable", help="Switch it on or off.")
    ] = None,
) -> None:
    """Change the vacation response; only what you give changes."""
    context = cli(ctx)
    patch: dict[str, Any] = {}
    text = body if body_file is None else _read(body_file)
    for key, value in (
        ("subject", subject),
        ("textBody", text),
        ("htmlBody", html),
        ("fromDate", start),
        ("toDate", until),
        ("isEnabled", enabled),
    ):
        if value is not None:
            # An empty string clears a field: the server wants null, not "".
            patch[key] = None if value == "" else value
    if not patch:
        raise UsageError("nothing to change", hint="give --body, --enable, ...")
    _update(context, patch, "updated the vacation response")


@app.command()
def enable(ctx: typer.Context) -> None:
    """Switch the vacation response on."""
    _update(cli(ctx), {"isEnabled": True}, "the vacation response is on")


@app.command()
def disable(ctx: typer.Context) -> None:
    """Switch the vacation response off."""
    _update(cli(ctx), {"isEnabled": False}, "the vacation response is off")


def _update(context: CLIContext, patch: dict[str, Any], done: str) -> None:
    client = context.require("VacationResponse/set")
    with client.batch() as batch:
        handle = batch.add("VacationResponse/set", {"update": {_SINGLETON: patch}})
    report_set(context, handle.result, done)


def _read(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"cannot read {path}: {error.strerror or error}") from error
