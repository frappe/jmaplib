"""``jmap server``: what the server says about itself.

``capabilities`` and ``conformance`` read the session alone; neither calls a
method. ``conformance`` replaces ``python -m jmap.testing.conformance``.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, json_argument
from jmap.cli.output import Column, OutputFormat, View
from jmap.testing.conformance import CapabilityRow, analyse

app: typer.Typer = typer.Typer(no_args_is_help=True)


@app.command()
def session(ctx: typer.Context) -> None:
    """Show the session resource; --json prints it whole."""
    context = cli(ctx)
    client = context.client()
    raw = client.session.raw
    context.renderer.render(
        View(
            items=[raw],
            columns=[
                Column("Username", "username"),
                Column("API", "apiUrl"),
                Column("Download", "downloadUrl"),
                Column("Upload", "uploadUrl"),
                Column("Event source", "eventSourceUrl"),
                Column("State", "state"),
                Column("Accounts", "accounts", lambda s: str(len(s.get("accounts") or {}))),
                Column(
                    "Capabilities", "capabilities", lambda s: str(len(s.get("capabilities") or {}))
                ),
            ],
            single=True,
        )
    )


@app.command()
def capabilities(ctx: typer.Context) -> None:
    """List the capabilities the server advertises, and whether this build speaks them."""
    context = cli(ctx)
    client = context.client()
    report = analyse(client.session, client.capabilities, registry=client.registry)
    _render_rows(ctx, [row for row in report.rows if row.advertised])


@app.command()
def conformance(
    ctx: typer.Context,
    markdown: Annotated[bool, typer.Option("--markdown", help="Render as Markdown.")] = False,
) -> None:
    """Compare every capability this build models with what the server offers."""
    context = cli(ctx)
    client = context.client()
    report = analyse(client.session, client.capabilities, registry=client.registry)
    if markdown:
        context.out.file.write(report.markdown())
        return
    _render_rows(ctx, report.rows)


@app.command()
def echo(
    ctx: typer.Context,
    arguments: Annotated[
        str | None, typer.Argument(help="JSON object to echo, @FILE, or - for stdin.")
    ] = None,
) -> None:
    """Call Core/echo: proof that authentication and routing work."""
    context = cli(ctx)
    client = context.client()
    context.renderer.json(client.echo(**(json_argument(arguments, context) or {"ping": "jmap"})))


@app.command()
def refresh(ctx: typer.Context) -> None:
    """Fetch the session again and print its state."""
    context = cli(ctx)
    client = context.client()
    client.refresh_session()
    if context.renderer.format in {OutputFormat.JSON, OutputFormat.JSONL}:
        context.renderer.json({"state": client.session.state})
    else:
        context.out.file.write(client.session.state + "\n")


def _render_rows(ctx: typer.Context, rows: list[CapabilityRow]) -> None:
    items = [_row(row) for row in rows]
    cli(ctx).renderer.render(
        View(
            items=items,
            columns=[
                Column("CAPABILITY", "urn"),
                Column("STATUS", "status"),
                Column("SPEC", "reference"),
                Column("METHODS", "methods", lambda i: str(len(i["methods"])), justify="right"),
            ],
        )
    )


def _row(row: CapabilityRow) -> dict[str, Any]:
    return {
        "urn": row.urn,
        "status": row.status,
        "reference": row.reference,
        "experimental": row.experimental,
        "methods": list(row.methods),
    }
