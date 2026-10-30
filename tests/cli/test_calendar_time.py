"""Times and event parts as the calendar commands read them."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from jmap.cli.commands import calendar_time
from jmap.cli.commands.calendar_time import (
    Zone,
    duration,
    human_duration,
    iso_duration,
    moment,
    split,
)
from jmap.cli.commands.event_parts import alert, recurrence_rule
from jmap.cli.errors import UsageError

BASE = datetime(2026, 11, 2, 15, 30)
LONDON = Zone.named("Europe/London")


class TestZone:
    def test_given_wins(self):
        assert Zone.chosen("Asia/Tokyo", {"TZ": "Europe/London"}).name == "Asia/Tokyo"

    def test_then_tz(self):
        assert Zone.chosen(None, {"TZ": ":Europe/Paris"}).name == "Europe/Paris"

    def test_then_the_machine(self, monkeypatch, tmp_path):
        target = tmp_path / "zoneinfo" / "America" / "Denver"
        target.parent.mkdir(parents=True)
        target.write_text("")
        link = tmp_path / "localtime"
        os.symlink(target, link)
        monkeypatch.setattr(calendar_time, "LOCALTIME", str(link))
        assert Zone.chosen(None, {}).name == "America/Denver"

    def test_else_utc(self, monkeypatch, tmp_path):
        monkeypatch.setattr(calendar_time, "LOCALTIME", str(tmp_path / "nothing"))
        assert Zone.chosen(None, {}).name == "Etc/UTC"

    def test_unknown(self):
        with pytest.raises(UsageError, match="not a time zone"):
            Zone.named("Nowhere/Special")

    def test_conversions(self):
        instant = LONDON.utc(datetime(2026, 7, 1, 12))
        assert instant.hour == 11
        assert LONDON.local(instant) == datetime(2026, 7, 1, 12)
        assert LONDON.now().tzinfo is None


class TestMoment:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("today", datetime(2026, 11, 2)),
            ("Tomorrow", datetime(2026, 11, 3)),
            ("yesterday", datetime(2026, 11, 1)),
            ("+90m", BASE + timedelta(minutes=90)),
            ("-2h", BASE - timedelta(hours=2)),
            ("+1w", BASE + timedelta(weeks=1)),
            ("2026-12-01", datetime(2026, 12, 1)),
            ("2026-12-01 09:15", datetime(2026, 12, 1, 9, 15)),
        ],
    )
    def test_forms(self, text, expected):
        assert moment(text, base=BASE) == expected


class TestSplit:
    def test_no_bound(self):
        assert split(BASE, BASE + timedelta(days=9), None) == [(BASE, BASE + timedelta(days=9))]

    def test_a_zero_bound_is_none(self):
        assert len(split(BASE, BASE + timedelta(days=9), 0)) == 1

    def test_pieces(self):
        pieces = split(BASE, BASE + timedelta(days=5), 2 * 86400)
        assert [(b - a).days for a, b in pieces] == [2, 2, 1]


class TestDuration:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1h30m", "PT1H30M"),
            ("90m", "PT1H30M"),
            ("2d", "P2D"),
            ("1w1d", "P8D"),
            ("1d2h3m4s", "P1DT2H3M4S"),
            ("pt45m", "PT45M"),
            ("0s", "PT0S"),
        ],
    )
    def test_forms(self, text, expected):
        assert duration(text) == expected

    @pytest.mark.parametrize("text", ["", "an hour", "P", "P1Y"])
    def test_refused(self, text):
        with pytest.raises(UsageError, match="not a duration"):
            duration(text)

    def test_iso(self):
        assert iso_duration(timedelta(0)) == "PT0S"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("PT1H30M", "1h30m"), ("P1W", "1w"), ("PT0S", "0m"), (None, ""), ("P1Y", "P1Y")],
    )
    def test_human(self, value, expected):
        assert human_duration(value) == expected


class TestRule:
    def test_a_bare_frequency(self):
        assert recurrence_rule("Weekly", LONDON).to_wire() == {"frequency": "weekly"}

    def test_every_part(self):
        rule = recurrence_rule(
            "RRULE:FREQ=MONTHLY;INTERVAL=2;BYDAY=-1FR,2MO;BYMONTH=1,5L;BYMONTHDAY=1,-1;"
            "BYSETPOS=1;WKST=SU;UNTIL=20261231",
            LONDON,
        )
        assert rule.to_wire() == {
            "frequency": "monthly",
            "interval": 2,
            "byDay": [{"day": "fr", "nthOfPeriod": -1}, {"day": "mo", "nthOfPeriod": 2}],
            "byMonth": ["1", "5L"],
            "byMonthDay": [1, -1],
            "bySetPosition": [1],
            "firstDayOfWeek": "su",
            "until": "2026-12-31T00:00:00",
        }

    def test_a_utc_until_is_read_in_the_zone(self):
        rule = recurrence_rule("FREQ=DAILY;UNTIL=20260701T120000Z", LONDON)
        assert rule.to_wire()["until"] == "2026-07-01T13:00:00"

    def test_a_local_until(self):
        rule = recurrence_rule("FREQ=DAILY;UNTIL=20260701T120000", LONDON)
        assert rule.to_wire()["until"] == "2026-07-01T12:00:00"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("FREQ=FORTNIGHTLY", "FREQ=FORTNIGHTLY is not valid"),
            ("FREQ=DAILY;COUNT=x", "COUNT=x is not valid"),
            ("FREQ=DAILY;BYDAY=XX", "BYDAY=XX is not valid"),
            ("FREQ=DAILY;COLOR=red", "'COLOR' is not known"),
            ("FREQ=DAILY;COUNT=2;UNTIL=20261231", "COUNT or UNTIL"),
            ("INTERVAL=2", "has no FREQ"),
        ],
    )
    def test_refused(self, text, message):
        with pytest.raises(UsageError, match=message):
            recurrence_rule(text, LONDON)


class TestAlert:
    @pytest.mark.parametrize(
        ("text", "offset"),
        [("15m", "-PT15M"), ("1d", "-P1D"), ("-pt1h", "-PT1H"), ("+PT5M", "PT5M")],
    )
    def test_forms(self, text, offset):
        assert alert(text).to_wire() == {"trigger": {"offset": offset}}
