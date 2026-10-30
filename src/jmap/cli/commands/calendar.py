"""``jmap calendar``: list, create, rename and delete calendars."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of

app: typer.Typer = typer.Typer(no_args_is_help=True)

CalendarRef = Annotated[str, typer.Argument(help="The calendar, by name or id.")]

_COLUMNS = [
    Column("NAME", "name"),
    Column("DEFAULT", "isDefault", lambda c: "yes" if c.get("isDefault") else ""),
    Column("COLOR", "color"),
    Column("TIME ZONE", "timeZone"),
    Column("ID", "id"),
]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List your calendars."""
    context = cli(ctx)
    context.renderer.render(View(load_calendars(context), _COLUMNS))


@app.command()
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="The new calendar's name.")],
    # Not --color: that is the global option for colored output.
    color: Annotated[
        str | None, typer.Option("--css-color", help="Its color: red or #3366ff.")
    ] = None,
    description: Annotated[str | None, typer.Option(help="What it is for.")] = None,
    time_zone: Annotated[
        str | None, typer.Option("--tz", help="Its time zone: Europe/London.")
    ] = None,
    default: Annotated[
        bool, typer.Option("--default", help="Make it the default calendar.")
    ] = False,
) -> None:
    """Create a calendar."""
    context = cli(ctx)
    calendar: dict[str, Any] = {"name": name}
    for key, value in (("color", color), ("description", description), ("timeZone", time_zone)):
        if value is not None:
            calendar[key] = value
    arguments: dict[str, Any] = {"create": {"new": calendar}}
    if default:
        arguments["onSuccessSetIsDefault"] = "#new"
    _set(context, arguments, f"created the calendar {name}")


@app.command()
def rename(
    ctx: typer.Context,
    calendar: CalendarRef,
    name: Annotated[str, typer.Argument(help="The new name.")],
) -> None:
    """Rename a calendar."""
    context = cli(ctx)
    found = find_calendar(load_calendars(context), calendar)
    _set(context, {"update": {found["id"]: {"name": name}}}, f"renamed {found['name']} to {name}")


@app.command()
def delete(
    ctx: typer.Context,
    calendar: CalendarRef,
    with_events: Annotated[
        bool, typer.Option("--with-events", help="Also delete the events in it.")
    ] = False,
) -> None:
    """Delete a calendar, after you confirm. One with events needs --with-events."""
    context = cli(ctx)
    found = find_calendar(load_calendars(context), calendar)
    context.confirm(f"Delete the calendar {found['name']}?")
    arguments: dict[str, Any] = {"destroy": [found["id"]]}
    if with_events:
        arguments["onDestroyRemoveEvents"] = True
    _set(context, arguments, f"deleted {found['name']}")


def load_calendars(context: CLIContext) -> list[dict[str, Any]]:
    """Every calendar in the account."""
    client = context.require("Calendar/get")
    with client.batch() as batch:
        handle = batch.add("Calendar/get", {"ids": None})
    items: list[dict[str, Any]] = wire_of(handle.result)["list"]
    return items


def find_calendar(calendars: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """The calendar ``wanted`` names, by id or by name in any case."""
    for item in calendars:
        if item.get("id") == wanted:
            return item
    folded = wanted.casefold()
    named = [item for item in calendars if str(item.get("name", "")).casefold() == folded]
    if len(named) > 1:
        ids = ", ".join(str(item["id"]) for item in named)
        raise UsageError(f"{len(named)} calendars are called {wanted!r}: {ids}", hint="use the id")
    if not named:
        raise NotFoundError(f"no calendar {wanted!r}", hint="see 'jmap calendar list'")
    return named[0]


def default_calendar(calendars: list[dict[str, Any]]) -> dict[str, Any]:
    """The default calendar, or the first one if none is marked."""
    if not calendars:
        raise NotFoundError("there are no calendars", hint="create one with 'jmap calendar create'")
    return next((item for item in calendars if item.get("isDefault")), calendars[0])


def calendar_names(calendars: list[dict[str, Any]]) -> dict[str, str]:
    """Calendar id -> name, for showing which calendar an event is in."""
    return {str(item["id"]): str(item.get("name") or item["id"]) for item in calendars}


def _set(context: CLIContext, arguments: dict[str, Any], done: str) -> None:
    client = context.require("Calendar/set")
    with client.batch() as batch:
        handle = batch.add("Calendar/set", arguments)
    report_set(context, handle.result, done)
