"""How an event reads in a table: its start, length, places, people and rule."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jmap.cli.commands.calendar_time import Zone
from jmap.core.ijson import parse_utc_date
from jmap.core.narrow import as_list, as_object


def when(event: Any, zone: Zone) -> str:
    """When an event starts, in ``zone`` when the server gave the instant."""
    if event.get("showWithoutTime"):
        return str(event.get("start") or "")[:10]
    utc_start = event.get("utcStart")
    if utc_start:
        return zone.local(parse_utc_date(utc_start)).strftime("%Y-%m-%d %H:%M")
    return str(event.get("start") or "").replace("T", " ")[:16]


def start_key(event: dict[str, Any]) -> datetime:
    """What a listing sorts on: the instant if the server gave it, else the wall clock."""
    utc_start = event.get("utcStart")
    if utc_start:
        return parse_utc_date(utc_start).replace(tzinfo=None)
    return datetime.fromisoformat(str(event.get("start") or "0001-01-01"))


def calendars(event: Any, names: dict[str, str]) -> str:
    ids = [key for key, value in as_map(event.get("calendarIds")).items() if value]
    return ", ".join(names.get(key, key) for key in ids)


def locations(event: Any) -> str:
    places = as_map(event.get("locations")).values()
    return ", ".join(str(place.get("name") or "") for place in places)


def repeats(event: Any) -> str:
    """A recurrence rule in a few words: ``weekly, on mo, times 4``."""
    rule = event.get("recurrenceRule")
    if not rule:
        return ""
    text = str(rule.get("frequency"))
    days = ",".join(str(day.get("day")) for day in as_list(rule.get("byDay") or []))
    for label, value in (
        ("every", rule.get("interval")),
        ("on", days),
        ("times", rule.get("count")),
        ("until", rule.get("until")),
    ):
        if value:
            text += f", {label} {value}"
    return text


def participants(event: Any) -> str:
    people = as_map(event.get("participants")).values()
    return ", ".join(
        f"{person.get('name') or person.get('email') or person.get('calendarAddress')}"
        f" ({person.get('participationStatus') or 'needs-action'})"
        for person in people
    )


def alerts(event: Any) -> str:
    triggers = [as_map(item.get("trigger")) for item in as_map(event.get("alerts")).values()]
    return ", ".join(str(trigger.get("offset") or trigger.get("when")) for trigger in triggers)


def changed_by(notification: Any) -> str:
    person = as_map(notification.get("changedBy"))
    return str(person.get("name") or person.get("email") or "")


def as_map(value: Any) -> dict[str, Any]:
    """A JSON object that may be absent or null, as a dict."""
    return as_object(value or {})
