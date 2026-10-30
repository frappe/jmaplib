"""``jmap quota``: how much of each limit is used (RFC 9425).

``jmap quota`` alone is ``jmap quota list``. A server only returns a quota whose
types belong to capabilities in the request's ``using``, so the request names
every capability this build and the server share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap.cli import format as fmt
from jmap.cli.commands import cli
from jmap.cli.errors import NotFoundError
from jmap.cli.output import Column, View, wire_of
from jmap.models.quota import ResourceType

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext

app: typer.Typer = typer.Typer(invoke_without_command=True)


@app.callback()
def quota(ctx: typer.Context) -> None:
    """Your quotas: used, and the limit."""
    if ctx.invoked_subcommand is None:
        _render(cli(ctx), _quotas(cli(ctx)))


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List every quota, with what is used against its limit."""
    context = cli(ctx)
    _render(context, _quotas(context))


@app.command()
def show(
    ctx: typer.Context,
    which: Annotated[str, typer.Argument(help="The quota, by id or name.")],
) -> None:
    """Show one quota in full."""
    context = cli(ctx)
    for item in _quotas(context):
        if which in (item.get("id"), item.get("name")):
            columns = [
                *_COLUMNS,
                Column("Warn at", "warnLimit", lambda i: _amount(i, "warnLimit")),
                Column("Soft limit", "softLimit", lambda i: _amount(i, "softLimit")),
                Column("Description", "description"),
            ]
            context.renderer.render(View([item], columns, single=True))
            return
    raise NotFoundError(f"no quota {which!r}", hint="see 'jmap quota list'")


def _amount(item: Mapping[str, Any], key: str) -> str:
    """Octets as a size, a count as a number, nothing as nothing."""
    value = item.get(key)
    if value is None:
        return ""
    if item.get("resourceType") == ResourceType.OCTETS:
        return fmt.size(value)
    return str(value)


_COLUMNS = [
    Column("ID", "id"),
    Column("NAME", "name"),
    Column("SCOPE", "scope"),
    Column("TYPES", "types", lambda i: ", ".join(i.get("types") or [])),
    Column("USED", "used", lambda i: _amount(i, "used"), justify="right"),
    Column("LIMIT", "hardLimit", lambda i: _amount(i, "hardLimit"), justify="right"),
]


def _render(context: CLIContext, quotas: list[dict[str, Any]]) -> None:
    context.renderer.render(View(quotas, _COLUMNS))


def _quotas(context: CLIContext) -> list[dict[str, Any]]:
    client = context.require("Quota/get")
    with client.batch(extra_using=frozenset(client.capabilities.specs)) as batch:
        handle = batch.add("Quota/get", {"ids": None})
    quotas: list[dict[str, Any]] = wire_of(handle.result)["list"]
    return quotas
