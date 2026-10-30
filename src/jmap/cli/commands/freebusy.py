"""``jmap freebusy``: when someone is busy, from ``Principal/getAvailability``."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli
from jmap.cli.commands.calendar_time import Zone, window
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.core.ijson import format_utc_date, parse_utc_date
from jmap.core.narrow import as_object


def freebusy(
    ctx: typer.Context,
    principal: Annotated[str, typer.Argument(help="Whose time: an email address or an id.")],
    start: Annotated[str, typer.Option("--from", help="Start: today, +1d, 2026-11-01.")] = "today",
    end: Annotated[str, typer.Option("--to", help="End; +7d counts from --from.")] = "+7d",
    time_zone: Annotated[
        str | None, typer.Option("--tz", help="Read and show times in this zone.")
    ] = None,
    details: Annotated[
        bool, typer.Option("--details", help="Show the events too, where you may see them.")
    ] = False,
) -> None:
    """Show when a person or resource is busy."""
    context = cli(ctx)
    zone = Zone.chosen(time_zone, context.environ)
    begins, ends = window(start, end, zone)
    principal_id = _principal_id(context, principal)
    client = context.require("Principal/getAvailability")
    with client.batch() as batch:
        handle = batch.principals.principal.get_availability(
            id=principal_id,
            utc_start=format_utc_date(zone.utc(begins)),
            utc_end=format_utc_date(zone.utc(ends)),
            show_details=details,
        )
    periods: list[dict[str, Any]] = wire_of(handle.result)["list"]
    columns = [
        Column("START", "utcStart", lambda p: _local(p.get("utcStart"), zone)),
        Column("END", "utcEnd", lambda p: _local(p.get("utcEnd"), zone)),
        Column("STATUS", "busyStatus", lambda p: str(p.get("busyStatus") or "unavailable")),
    ]
    if details:
        columns.append(
            Column(
                "EVENT", "event", lambda p: str(as_object(p.get("event") or {}).get("title") or "")
            )
        )
    context.renderer.render(View(periods, columns))


def _principal_id(context: CLIContext, wanted: str) -> str:
    """The id of the principal ``wanted`` names; an address is looked up."""
    if "@" not in wanted:
        return wanted
    client = context.require("Principal/query")
    with client.batch() as batch:
        handle = batch.add("Principal/query", {"filter": {"email": wanted}})
    ids: list[str] = wire_of(handle.result)["ids"]
    if not ids:
        raise NotFoundError(f"no principal has the address {wanted}")
    if len(ids) > 1:
        raise UsageError(f"{len(ids)} principals have the address {wanted}: {', '.join(ids)}")
    return ids[0]


def _local(value: Any, zone: Zone) -> str:
    if not value:
        return ""
    return zone.local(parse_utc_date(str(value))).strftime("%Y-%m-%d %H:%M")
