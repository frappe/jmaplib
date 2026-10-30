"""The parts of an event given as options: ``--rrule``, ``--alert``, ``--attendee``."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from jmap.cli.commands.calendar_time import duration
from jmap.cli.errors import UsageError
from jmap.mail.addresses import parse_addresses
from jmap.models.jscalendar import Alert, OffsetTrigger, Participant, RecurrenceRule

if TYPE_CHECKING:
    from jmap.cli.commands.calendar_time import Zone

_FREQUENCIES: Final = frozenset(
    {"yearly", "monthly", "weekly", "daily", "hourly", "minutely", "secondly"}
)
_NDAY: Final = re.compile(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)")
#: RRULE parts that are lists of numbers, and their JSCalendar names.
_NUMBERS: Final = {
    "BYMONTHDAY": "byMonthDay",
    "BYYEARDAY": "byYearDay",
    "BYWEEKNO": "byWeekNo",
    "BYHOUR": "byHour",
    "BYMINUTE": "byMinute",
    "BYSECOND": "bySecond",
    "BYSETPOS": "bySetPosition",
}
#: RRULE parts whose value is a lowercase word.
_WORDS: Final = {"WKST": "firstDayOfWeek", "RSCALE": "rscale", "SKIP": "skip"}


def recurrence_rule(text: str, zone: Zone) -> RecurrenceRule:
    """An RFC 5545 RRULE as a JSCalendar rule: ``FREQ=WEEKLY;BYDAY=MO;COUNT=4``.

    A bare frequency, such as ``weekly``, is enough too. A UTC ``UNTIL`` becomes
    the wall-clock time in ``zone``, the event's zone.
    """
    body = text.strip().removeprefix("RRULE:")
    if body.lower() in _FREQUENCIES:
        return RecurrenceRule(frequency=body.lower())
    rule: dict[str, Any] = {}
    for part in filter(None, body.split(";")):
        key, _, value = part.partition("=")
        _put(rule, key.strip().upper(), value.strip(), zone)
    if "frequency" not in rule:
        raise UsageError(f"{text!r} has no FREQ", hint="for example FREQ=WEEKLY;COUNT=4")
    if "count" in rule and "until" in rule:
        raise UsageError("a rule takes COUNT or UNTIL, not both")
    return RecurrenceRule.model_validate(rule)


def alert(text: str) -> Alert:
    """A reminder before the start: ``15m``, ``1h``, ``1d``; ``-PT15M`` as it is."""
    signed = text.strip().upper()
    if signed.startswith(("-P", "+P")):
        offset = signed[0] + duration(signed[1:])
    else:
        offset = "-" + duration(text)
    return Alert(trigger=OffsetTrigger(offset=offset.removeprefix("+")))


def attendees(texts: list[str]) -> list[Participant]:
    """Each address in ``texts`` as an attendee who is asked to reply."""
    found = [address for text in texts for address in parse_addresses(text)]
    return [
        Participant.model_validate(
            {
                **({"name": address.name} if address.name else {}),
                "email": address.email,
                "calendarAddress": f"mailto:{address.email}",
                "kind": "individual",
                "roles": {"attendee": True},
                "participationStatus": "needs-action",
                "expectReply": True,
            }
        )
        for address in found
    ]


def organizer(calendar_address: str, name: str | None) -> Participant:
    """You, as the owner of an event you invite people to."""
    return Participant.model_validate(
        {
            **({"name": name} if name else {}),
            "calendarAddress": calendar_address,
            "kind": "individual",
            "roles": {"owner": True, "attendee": True},
            "participationStatus": "accepted",
        }
    )


def _put(rule: dict[str, Any], key: str, value: str, zone: Zone) -> None:
    """Add one ``KEY=value`` part to ``rule``, in JSCalendar's names and shapes."""
    try:
        if key == "FREQ":
            rule["frequency"] = _frequency(value)
        elif key in {"INTERVAL", "COUNT"}:
            rule[key.lower()] = int(value)
        elif key == "UNTIL":
            rule["until"] = _until(value, zone)
        elif key == "BYDAY":
            rule["byDay"] = [_nday(day) for day in value.upper().split(",")]
        elif key == "BYMONTH":
            rule["byMonth"] = value.split(",")
        elif key in _NUMBERS:
            rule[_NUMBERS[key]] = [int(number) for number in value.split(",")]
        elif key in _WORDS:
            rule[_WORDS[key]] = value.lower()
        else:
            raise UsageError(f"the rule part {key!r} is not known")
    except ValueError:
        raise UsageError(f"{key}={value} is not valid in a rule") from None


def _frequency(value: str) -> str:
    if value.lower() not in _FREQUENCIES:
        raise ValueError(value)
    return value.lower()


def _nday(text: str) -> dict[str, Any]:
    found = _NDAY.fullmatch(text.strip())
    if found is None:
        raise ValueError(text)
    day: dict[str, Any] = {"day": found[2].lower()}
    if found[1]:
        day["nthOfPeriod"] = int(found[1])
    return day


def _until(value: str, zone: Zone) -> str:
    """``20261231`` or ``20261231T090000[Z]`` as a LocalDateTime in ``zone``."""
    utc = value.endswith("Z")
    digits = value.removesuffix("Z")
    form = "%Y%m%dT%H%M%S" if "T" in digits else "%Y%m%d"
    wall = datetime.strptime(digits, form)
    if utc:
        wall = zone.local(wall.replace(tzinfo=UTC))
    return wall.strftime("%Y-%m-%dT%H:%M:%S")
