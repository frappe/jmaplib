"""``jmap event create``, ``import`` and ``update``."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from jmap.cli.commands import cli
from jmap.cli.commands.calendar import default_calendar, find_calendar, load_calendars
from jmap.cli.commands.calendar_time import Zone, duration, iso_duration, moment
from jmap.cli.commands.event import (
    EventRef,
    ThisOccurrence,
    TimeZone,
    app,
    fetch_event,
    override,
    refuse_occurrence,
    set_event,
)
from jmap.cli.commands.event_parts import alert, attendees, organizer, recurrence_rule
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import wire_of
from jmap.core.ijson import format_local_date
from jmap.models.calendars import CalendarEvent
from jmap.models.jscalendar import Location, Participant

Calendar = Annotated[str | None, typer.Option("--calendar", help="The calendar, by name or id.")]
Start = Annotated[str | None, typer.Option(help="When it starts: 2026-11-01T09:30, tomorrow.")]
Length = Annotated[str | None, typer.Option("--duration", help="How long: 45m, 1h30m, 2d.")]
Place = Annotated[str | None, typer.Option(help="Where it is.")]
About = Annotated[str | None, typer.Option(help="What it is about.")]
SendInvites = Annotated[bool, typer.Option("--send-invites", help="Email the attendees about it.")]

#: Properties the server sets, dropped from a parsed event before it is stored.
_SERVER_SET: Final = frozenset({"id", "baseEventId", "isOrigin", "utcStart", "utcEnd", "myRights"})
_CALENDAR_PROPERTIES: Final = ["id", "name", "isDefault"]


@app.command()
def create(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="What the event is called.")],
    start: Annotated[str, typer.Option(help="When it starts: 2026-11-01T09:30, tomorrow.")],
    length: Length = None,
    end: Annotated[str | None, typer.Option("--end", help="When it ends, instead.")] = None,
    all_day: Annotated[bool, typer.Option("--all-day", help="Whole days, no time.")] = False,
    time_zone: TimeZone = None,
    calendar: Calendar = None,
    location: Place = None,
    description: About = None,
    rrule: Annotated[
        str | None, typer.Option("--rrule", help="How it repeats: weekly, FREQ=DAILY;COUNT=5.")
    ] = None,
    alerts: Annotated[
        list[str] | None, typer.Option("--alert", help="Remind this long before: 15m (repeatable).")
    ] = None,
    invitees: Annotated[
        list[str] | None, typer.Option("--attendee", help="Invite this address (repeatable).")
    ] = None,
    send_invites: SendInvites = False,
) -> None:
    """Create an event, one hour long unless you say otherwise."""
    if length is not None and end is not None:
        raise UsageError("give --duration or --end, not both")
    context = cli(ctx)
    zone = Zone.chosen(time_zone, context.environ)
    begins = moment(start, base=_midnight(zone))
    if all_day:
        begins = datetime.combine(begins.date(), time())
    fields: dict[str, Any] = {
        "title": title,
        "start": begins,
        "duration": _length(begins, length, end, all_day=all_day),
        "description": description,
        "locations": {"1": Location(name=location)} if location else None,
        "recurrence_rule": recurrence_rule(rrule, zone) if rrule else None,
        "alerts": {str(n): alert(text) for n, text in enumerate(alerts or [], 1)} or None,
    }
    if all_day:
        fields["show_without_time"] = True
    else:
        fields["time_zone"] = zone.name
    client = context.require("Calendar/get", "ParticipantIdentity/get", "CalendarEvent/set")
    with client.batch() as batch:
        calendars = batch.add("Calendar/get", {"ids": None, "properties": _CALENDAR_PROPERTIES})
        identities = batch.add("ParticipantIdentity/get", {"ids": None})
    known: list[dict[str, Any]] = wire_of(calendars.result)["list"]
    chosen = find_calendar(known, calendar) if calendar else default_calendar(known)
    if invitees:
        fields.update(_invitations(context, wire_of(identities.result)["list"], invitees))
    event = CalendarEvent(
        calendar_ids={chosen["id"]: True}, **{k: v for k, v in fields.items() if v is not None}
    )
    arguments: dict[str, Any] = {"create": {"event": event.to_wire()}}
    if send_invites:
        arguments["sendSchedulingMessages"] = True
    response = set_event(context, arguments, f"created {title} in {chosen.get('name')}")
    context.renderer.note(f"its id is {response['created']['event']['id']}")
    if invitees and not send_invites:
        context.renderer.note("no invitations were sent; --send-invites sends them")


@app.command("import")
def import_(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="An iCalendar file (.ics), or - for stdin.")],
    calendar: Calendar = None,
) -> None:
    """Add the events in an iCalendar file to a calendar."""
    context = cli(ctx)
    data = _read(context, source)
    client = context.require("Calendar/get", "CalendarEvent/parse", "CalendarEvent/set")
    blob = client.upload(data, content_type="text/calendar").blob_id
    with client.batch() as batch:
        calendars = batch.add("Calendar/get", {"ids": None, "properties": _CALENDAR_PROPERTIES})
        parsed = batch.add("CalendarEvent/parse", {"blobIds": [blob]})
    known: list[dict[str, Any]] = wire_of(calendars.result)["list"]
    chosen = find_calendar(known, calendar) if calendar else default_calendar(known)
    events: list[dict[str, Any]] = [event.to_wire() for event in parsed.result.events_of(blob)]
    if not events:
        raise NotFoundError(f"found no events in {source}")
    create = {
        f"e{number}": {
            **{key: value for key, value in event.items() if key not in _SERVER_SET},
            "calendarIds": {chosen["id"]: True},
        }
        for number, event in enumerate(events, 1)
    }
    set_event(context, {"create": create}, f"imported {{n}} event(s) into {chosen.get('name')}")


@app.command()
def update(
    ctx: typer.Context,
    event: EventRef,
    title: Annotated[str | None, typer.Option(help="A new title.")] = None,
    start: Start = None,
    length: Length = None,
    time_zone: Annotated[
        str | None, typer.Option("--tz", help="Move it to this time zone.")
    ] = None,
    location: Place = None,
    description: About = None,
    status: Annotated[str | None, typer.Option(help="confirmed, tentative or cancelled.")] = None,
    calendar: Calendar = None,
    send_invites: SendInvites = False,
    this_occurrence: ThisOccurrence = False,
) -> None:
    """Change an event; only what you give changes. One occurrence needs --this-occurrence."""
    context = cli(ctx)
    found = fetch_event(context, event, ["id", "title", "baseEventId", "recurrenceId"])
    refuse_occurrence(found, this_occurrence=this_occurrence)
    if calendar is not None and this_occurrence:
        raise UsageError("one occurrence cannot move to another calendar")
    zone = Zone.chosen(time_zone, context.environ)
    patch: dict[str, Any] = {}
    for key, value in (
        ("title", title),
        ("start", format_local_date(moment(start, base=_midnight(zone))) if start else None),
        ("duration", duration(length) if length else None),
        ("timeZone", zone.name if time_zone else None),
        ("locations", {"1": {"name": location}} if location else None),
        ("description", description),
        ("status", status),
    ):
        if value is not None:
            patch[key] = value
    if calendar is not None:
        patch["calendarIds"] = {find_calendar(load_calendars(context), calendar)["id"]: True}
    if not patch:
        raise UsageError("nothing to change", hint="give --title, --start, ...")
    arguments: dict[str, Any]
    if this_occurrence:
        arguments = override(context, found, patch)
    else:
        arguments = {"update": {found["id"]: patch}}
    if send_invites:
        arguments["sendSchedulingMessages"] = True
    set_event(context, arguments, f"updated {found.get('title') or found['id']}")


def _length(begins: datetime, length: str | None, end: str | None, *, all_day: bool) -> str:
    """The event's Duration, from ``--duration`` or ``--end``."""
    if length is not None:
        return duration(length)
    if end is not None:
        ends = moment(end, base=begins)
        if ends <= begins:
            raise UsageError("--end must come after --start")
        return iso_duration(ends - begins)
    return "P1D" if all_day else "PT1H"


def _invitations(
    context: CLIContext, identities: list[dict[str, Any]], invitees: list[str]
) -> dict[str, Any]:
    """The organizer and participants of an event you invite people to.

    You are the organizer, as your default scheduling identity, or as your
    username when the server lists none.
    """
    me = next((item for item in identities if item.get("isDefault")), None)
    me = me or (identities[0] if identities else {})
    address = me.get("calendarAddress") or f"mailto:{context.client().session.username}"
    people: dict[str, Participant] = {"0": organizer(address, me.get("name"))}
    for number, person in enumerate(attendees(invitees), 1):
        people[str(number)] = person
    return {"organizer_calendar_address": address, "participants": people}


def _midnight(zone: Zone) -> datetime:
    return datetime.combine(zone.now().date(), time())


def _read(context: CLIContext, source: str) -> bytes:
    if source == "-":
        buffer = getattr(context.stdin, "buffer", None)
        return buffer.read() if buffer is not None else context.stdin.read().encode("utf-8")
    try:
        return Path(source).expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {source}: {error.strerror or error}") from error
