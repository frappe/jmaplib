"""Times as the calendar commands take them: zones, ``today``, ``+7d``, ``1h30m``.

A calendar query reads its bounds as wall-clock times in a named zone, so the
commands work in naive datetimes plus an IANA zone name, never in fixed offsets.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jmap.capabilities.calendars import duration_seconds
from jmap.cli.errors import UsageError

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The link that names this machine's zone on Linux and macOS.
LOCALTIME: Final = "/etc/localtime"

_RELATIVE: Final = re.compile(r"([+-])(\d+)([mhdw])")
_SPAN: Final = re.compile(r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_UNIT_SECONDS: Final = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
_WORDS: Final = {"today": 0, "tomorrow": 1, "yesterday": -1}


@dataclass(frozen=True, slots=True)
class Zone:
    """An IANA time zone, by name and as a ``tzinfo``."""

    name: str
    info: ZoneInfo

    @classmethod
    def named(cls, name: str) -> Zone:
        try:
            return cls(name, ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            raise UsageError(
                f"{name!r} is not a time zone", hint="use an IANA name: Europe/London"
            ) from None

    @classmethod
    def chosen(cls, given: str | None, environ: Mapping[str, str]) -> Zone:
        """``--tz``, else ``$TZ``, else this machine's zone, else UTC."""
        return cls.named(given or environ.get("TZ", "").lstrip(":") or _machine_zone())

    def now(self) -> datetime:
        """The wall-clock time now in this zone, as a naive datetime."""
        return datetime.now(self.info).replace(tzinfo=None)

    def local(self, instant: datetime) -> datetime:
        """An aware instant as this zone's wall clock, naive."""
        return instant.astimezone(self.info).replace(tzinfo=None)

    def utc(self, wall: datetime) -> datetime:
        """This zone's naive wall-clock time as a UTC instant."""
        return wall.replace(tzinfo=self.info).astimezone(UTC)


def moment(text: str, *, base: datetime) -> datetime:
    """The naive time ``text`` names.

    ``today``, ``tomorrow`` and ``yesterday`` are midnights around ``base``.
    ``+90m``, ``-2h``, ``+3d`` and ``+1w`` count from ``base``. Anything else is a date or
    a date and time: ``2026-11-01``, ``2026-11-01T09:30``. A time with an offset
    is refused: the zone is a separate option.
    """
    word = text.strip().lower()
    if word in _WORDS:
        return datetime.combine(base.date(), time()) + timedelta(days=_WORDS[word])
    relative = _RELATIVE.fullmatch(word)
    if relative:
        sign = -1 if relative[1] == "-" else 1
        return base + sign * timedelta(seconds=int(relative[2]) * _UNIT_SECONDS[relative[3]])
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        raise UsageError(f"{text!r} is not a time: use 2026-11-01T09:30, today or +3d") from None
    if parsed.tzinfo is not None:
        raise UsageError(f"{text!r} has an offset", hint="give the wall-clock time and --tz")
    return parsed


def window(start: str, end: str, zone: Zone) -> tuple[datetime, datetime]:
    """The span ``--from start --to end`` names; ``end`` counts from ``start``."""
    begins = moment(start, base=datetime.combine(zone.now().date(), time()))
    ends = moment(end, base=begins)
    if ends <= begins:
        raise UsageError("--to must come after --from")
    return begins, ends


def split(start: datetime, end: datetime, bound: int | None) -> list[tuple[datetime, datetime]]:
    """``start``-``end`` as consecutive spans of at most ``bound`` seconds."""
    if bound is None or bound <= 0:
        return [(start, end)]
    step = timedelta(seconds=bound)
    spans: list[tuple[datetime, datetime]] = []
    while start < end:
        spans.append((start, min(start + step, end)))
        start += step
    return spans


def duration(text: str) -> str:
    """A JSCalendar Duration: ``1h30m`` is ``PT1H30M``; ``PT1H`` stays as it is."""
    upper = text.strip().upper()
    if upper.startswith("P"):
        if duration_seconds(upper) is None:
            raise UsageError(f"{text!r} is not a duration: use 1h30m, 2d or PT45M")
        return upper
    found = _SPAN.fullmatch(text.strip().lower())
    if not found or not any(found.groups()):
        raise UsageError(f"{text!r} is not a duration: use 1h30m, 2d or PT45M")
    weeks, days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return iso_duration(
        timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    )


def iso_duration(span: timedelta) -> str:
    """``span`` as a JSCalendar Duration: ``P1DT2H``, ``PT0S``."""
    days, rest = divmod(int(span.total_seconds()), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    clock = "".join(
        f"{value}{unit}" for value, unit in ((hours, "H"), (minutes, "M"), (seconds, "S")) if value
    )
    text = "P" + (f"{days}D" if days else "") + (f"T{clock}" if clock else "")
    return "PT0S" if text == "P" else text


def human_duration(value: str | None) -> str:
    """A Duration as people read it: ``PT1H30M`` is ``1h30m``."""
    seconds = duration_seconds(value) if value else None
    if seconds is None:
        return value or ""
    parts: list[str] = []
    for unit in ("w", "d", "h", "m", "s"):
        count, seconds = divmod(seconds, _UNIT_SECONDS[unit])
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts) or "0m"


def _machine_zone() -> str:
    """The zone ``/etc/localtime`` links to, or UTC when it names none.

    By path parts, not text: the separator is ``\\`` on Windows.
    """
    parts = Path(os.path.realpath(LOCALTIME)).parts
    if "zoneinfo" not in parts:
        return "Etc/UTC"
    return "/".join(parts[parts.index("zoneinfo") + 1 :]) or "Etc/UTC"
