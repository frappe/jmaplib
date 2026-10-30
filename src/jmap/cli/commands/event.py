"""``jmap event``: list, show and delete events, and read change notifications.

Creating, importing and changing events is in :mod:`event_edit`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Final

import typer

from jmap.capabilities.calendars import CALENDARS_URN, CalendarsCapability, duration_seconds
from jmap.cli.commands import cli, report_set
from jmap.cli.commands import event_view as view
from jmap.cli.commands.calendar import calendar_names, find_calendar, load_calendars
from jmap.cli.commands.calendar_time import Zone, human_duration, split, window
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.client import BatchContext, JMAPClient
from jmap.core.ijson import format_local_date
from jmap.core.invocation import Handle
from jmap.models.calendars import check_recurrence_id

app: typer.Typer = typer.Typer(no_args_is_help=True)

EventRef = Annotated[str, typer.Argument(help="The event's id, as 'jmap event list' shows it.")]
TimeZone = Annotated[
    str | None, typer.Option("--tz", help="The time zone: Europe/London. Default: this machine's.")
]
ThisOccurrence = Annotated[
    bool,
    typer.Option("--this-occurrence", help="Change only this occurrence of a repeating event."),
]

#: What a listing shows. Not ``recurrenceOverrides``: it may not be asked for
#: with ``utcStart``.
_LIST_PROPERTIES: Final = [
    "id",
    "baseEventId",
    "calendarIds",
    "title",
    "start",
    "duration",
    "timeZone",
    "showWithoutTime",
    "utcStart",
    "utcEnd",
    "status",
]


@app.command("list")
def list_(
    ctx: typer.Context,
    start: Annotated[
        str, typer.Option("--from", help="Start: today, tomorrow, +2d, 2026-11-01T09:00.")
    ] = "today",
    end: Annotated[str, typer.Option("--to", help="End; +7d counts from --from.")] = "+7d",
    calendars: Annotated[
        list[str] | None, typer.Option("--calendar", help="Only this calendar (repeatable).")
    ] = None,
    time_zone: TimeZone = None,
    no_expand: Annotated[
        bool, typer.Option("--no-expand", help="One row per repeating event, not per occurrence.")
    ] = False,
) -> None:
    """List events in a window of time, each occurrence of a repeating one on its own row."""
    context = cli(ctx)
    zone = Zone.chosen(time_zone, context.environ)
    begins, ends = window(start, end, zone)
    condition: dict[str, Any] = {}
    known: list[dict[str, Any]] | None = None
    if calendars:
        known = load_calendars(context)
        condition["inCalendars"] = [find_calendar(known, name)["id"] for name in calendars]
    client = context.require("CalendarEvent/query", "CalendarEvent/get", "Calendar/get")
    bound = None if no_expand else _expand_bound(client)
    names_of: Handle[Any] | None = None
    with client.batch() as batch:
        if known is None:
            names_of = batch.add("Calendar/get", {"ids": None, "properties": ["id", "name"]})
        handles = [
            _queue_window(batch, condition, span, zone, expand=not no_expand)
            for span in split(begins, ends, bound)
        ]
    listed = wire_of(names_of.result)["list"] if names_of is not None else known or []
    names = calendar_names(listed)
    context.renderer.render(
        View(
            _merged(handles),
            [
                Column("START", "start", lambda e: view.when(e, zone)),
                Column("LENGTH", "duration", lambda e: human_duration(e.get("duration"))),
                Column("TITLE", "title"),
                Column("CALENDAR", "calendarIds", lambda e: view.calendars(e, names)),
                Column("ID", "id"),
            ],
        )
    )


@app.command()
def show(ctx: typer.Context, event: EventRef, time_zone: TimeZone = None) -> None:
    """Show one event: when, where, who, and how it repeats."""
    context = cli(ctx)
    zone = Zone.chosen(time_zone, context.environ)
    client = context.require("CalendarEvent/get", "Calendar/get")
    with client.batch() as batch:
        names_of = batch.add("Calendar/get", {"ids": None, "properties": ["id", "name"]})
        handle = batch.add("CalendarEvent/get", {"ids": [event]})
    found = _only(handle, event)
    names = calendar_names(wire_of(names_of.result)["list"])
    columns = [
        Column("Title", "title"),
        Column("Start", "start", lambda e: view.when(e, zone)),
        Column("Time zone", "timeZone"),
        Column("Length", "duration", lambda e: human_duration(e.get("duration"))),
        Column("Status", "status"),
        Column("Calendars", "calendarIds", lambda e: view.calendars(e, names)),
        Column("Location", "locations", view.locations),
        Column("Repeats", "recurrenceRule", view.repeats),
        Column("Occurrence of", "baseEventId", lambda e: occurrence_of(e) or ""),
        Column("Participants", "participants", view.participants),
        Column("Alerts", "alerts", view.alerts),
        Column("Description", "description"),
        Column("ID", "id"),
    ]
    context.renderer.render(View([found], columns, single=True))


@app.command()
def delete(ctx: typer.Context, event: EventRef, this_occurrence: ThisOccurrence = False) -> None:
    """Delete an event, after you confirm. One occurrence needs --this-occurrence."""
    context = cli(ctx)
    found = fetch_event(context, event, ["id", "title", "baseEventId", "recurrenceId"])
    refuse_occurrence(found, this_occurrence=this_occurrence)
    title = found.get("title") or found["id"]
    if occurrence_of(found):
        context.confirm(f"Delete the occurrence {found.get('recurrenceId')} of {title}?")
        set_event(context, override(context, found, {"excluded": True}), "deleted the occurrence")
        return
    context.confirm(f"Delete the event {title}?")
    set_event(context, {"destroy": [found["id"]]}, f"deleted {title}")


@app.command()
def notifications(
    ctx: typer.Context,
    clear: Annotated[bool, typer.Option("--clear", help="Delete them all instead.")] = False,
) -> None:
    """List what others changed in events you can see, or clear the list."""
    context = cli(ctx)
    name = "CalendarEventNotification"
    client = context.require(f"{name}/query", f"{name}/get", f"{name}/set")
    with client.batch() as batch:
        found = batch.add(f"{name}/query", {})
        if clear:
            handle = batch.add(f"{name}/set", {"destroy": found.ref_ids()})
        else:
            handle = batch.add(f"{name}/get", {"ids": found.ref_ids()})
    if clear:
        report_set(context, handle.result, "cleared {n} notification(s)")
        return
    columns = [
        Column("CREATED", "created"),
        Column("TYPE", "type"),
        Column("BY", "changedBy", view.changed_by),
        Column("EVENT", "event", lambda n: str(view.as_map(n.get("event")).get("title") or "")),
        Column("COMMENT", "comment"),
        Column("ID", "id"),
    ]
    context.renderer.render(View(wire_of(handle.result)["list"], columns))


def fetch_event(context: CLIContext, event_id: str, properties: list[str]) -> dict[str, Any]:
    """One event, with only ``properties``."""
    client = context.require("CalendarEvent/get")
    with client.batch() as batch:
        handle = batch.add("CalendarEvent/get", {"ids": [event_id], "properties": properties})
    return _only(handle, event_id)


def occurrence_of(event: Mapping[str, Any]) -> str | None:
    """The repeating event ``event`` is one occurrence of, if it is one.

    Stalwart gives an event that is not an occurrence its own id as ``baseEventId``.
    """
    base = event.get("baseEventId")
    return str(base) if base and base != event.get("id") else None


def refuse_occurrence(event: dict[str, Any], *, this_occurrence: bool) -> None:
    """Stop a change to one occurrence that the person did not ask for by name."""
    base = occurrence_of(event)
    if base and not this_occurrence:
        raise UsageError(
            f"{event['id']} is one occurrence of the repeating event {base}",
            hint=f"pass --this-occurrence, or use {base} to change every occurrence",
        )
    if this_occurrence and not base:
        raise UsageError(f"{event['id']} is not one occurrence of a repeating event")


def override(
    context: CLIContext, occurrence: dict[str, Any], changes: dict[str, Any]
) -> dict[str, Any]:
    """The ``CalendarEvent/set`` arguments that apply ``changes`` to one occurrence.

    The change goes into the base event's ``recurrenceOverrides``, merged with
    any override the occurrence already has. Writing to the occurrence's own id
    instead would risk the server reading it as the whole series.
    """
    base = str(occurrence["baseEventId"])
    key = check_recurrence_id(str(occurrence.get("recurrenceId")))
    overrides = fetch_event(context, base, ["id", "recurrenceOverrides"]).get("recurrenceOverrides")
    if overrides is None:
        return {"update": {base: {"recurrenceOverrides": {key: changes}}}}
    merged = {**view.as_map(overrides.get(key)), **changes}
    return {"update": {base: {f"recurrenceOverrides/{key}": merged}}}


def set_event(context: CLIContext, arguments: dict[str, Any], done: str) -> dict[str, Any]:
    """Send one ``CalendarEvent/set`` and report it; returns its wire response."""
    client = context.require("CalendarEvent/set")
    with client.batch() as batch:
        handle = batch.add("CalendarEvent/set", arguments)
    report_set(context, handle.result, done)
    response: dict[str, Any] = wire_of(handle.result)
    return response


def _queue_window(
    batch: BatchContext,
    condition: dict[str, Any],
    span: tuple[datetime, datetime],
    zone: Zone,
    *,
    expand: bool,
) -> Handle[Any]:
    """Queue the query for one window and the ``/get`` of what it finds."""
    after, before = span
    window_condition = {
        **condition,
        "after": format_local_date(after),
        "before": format_local_date(before),
    }
    found = batch.add(
        "CalendarEvent/query",
        {"filter": window_condition, "expandRecurrences": expand, "timeZone": zone.name},
    )
    return batch.add(
        "CalendarEvent/get",
        {"ids": found.ref_ids(), "properties": _LIST_PROPERTIES, "timeZone": zone.name},
    )


def _only(handle: Handle[Any], event_id: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = wire_of(handle.result)["list"]
    if not found:
        raise NotFoundError(f"no event {event_id!r}", hint="see 'jmap event list'")
    return found[0]


def _expand_bound(client: JMAPClient) -> int | None:
    """``maxExpandedQueryDuration`` in seconds, if the account sets one."""
    session = client.session
    account = session.capability_account(CALENDARS_URN, client.default_account)
    limit = CalendarsCapability.of(session.capability_value(CALENDARS_URN, account))
    return duration_seconds(limit.max_expanded_query_duration or "")


def _merged(handles: list[Any]) -> list[dict[str, Any]]:
    """Events from each window once, in the order they start."""
    seen: dict[str, dict[str, Any]] = {}
    for handle in handles:
        for item in wire_of(handle.result)["list"]:
            seen.setdefault(str(item["id"]), item)
    return sorted(seen.values(), key=view.start_key)
