"""``jmap event create``, ``import`` and ``update``."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.app import run
from tests.fake.calendarstore import CalendarStore, event

from .conftest import text_of

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

LONDON = {"TZ": "Europe/London"}
ICS = (
    b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Gig\nEND:VEVENT\n"
    b"BEGIN:VEVENT\nSUMMARY:Talk\nEND:VEVENT\nEND:VCALENDAR\n"
)


@pytest.fixture
def cal(server: FakeJMAPServer, account: str) -> CalendarStore:
    calendars = CalendarStore(server)
    calendars.add(
        event("e1", "Dentist", "2026-11-03T09:00:00"),
        event(
            "e2",
            "Standup",
            "2026-11-02T10:00:00",
            calendar="c2",
            recurrenceRule={"frequency": "daily", "count": 3},
        ),
    )
    return calendars


def created(cal: CalendarStore) -> dict[str, Any]:
    body: dict[str, Any] = cal.last_set["create"]["event"]
    return body


class TestCreate:
    def test_an_hour_in_the_default_calendar(self, invoke, cal):
        result = invoke("event", "create", "Lunch", "--start", "2026-11-05T12:30", env=LONDON)
        assert result.code == 0, result.err
        assert "created Lunch in Personal" in result.err
        assert "its id is" in result.err
        assert created(cal) == {
            "calendarIds": {"c1": True},
            "title": "Lunch",
            "start": "2026-11-05T12:30:00",
            "duration": "PT1H",
            "timeZone": "Europe/London",
        }
        assert "sendSchedulingMessages" not in cal.last_set

    def test_every_part(self, invoke, cal):
        result = invoke(
            "event", "create", "Review", "--start", "2026-11-05T09:00", "--end", "+90m",
            "--tz", "Europe/Paris", "--calendar", "work", "--location", "Room 1",
            "--description", "Quarterly", "--rrule", "FREQ=WEEKLY;BYDAY=TH;COUNT=4",
            "--alert", "15m", "--alert", "-PT1H",
        )  # fmt: skip
        assert result.code == 0, result.err
        body = created(cal)
        assert body["calendarIds"] == {"c2": True}
        assert body["duration"] == "PT1H30M"
        assert body["timeZone"] == "Europe/Paris"
        assert body["locations"] == {"1": {"name": "Room 1"}}
        assert body["description"] == "Quarterly"
        assert body["recurrenceRule"] == {
            "frequency": "weekly",
            "byDay": [{"day": "th"}],
            "count": 4,
        }
        assert body["alerts"] == {
            "1": {"trigger": {"offset": "-PT15M"}},
            "2": {"trigger": {"offset": "-PT1H"}},
        }

    def test_all_day(self, invoke, cal):
        result = invoke("event", "create", "Holiday", "--start", "2026-12-24T15:00", "--all-day")
        assert result.code == 0, result.err
        body = created(cal)
        assert body["start"] == "2026-12-24T00:00:00"
        assert body["duration"] == "P1D"
        assert body["showWithoutTime"] is True
        assert "timeZone" not in body

    def test_duration(self, invoke, cal):
        invoke("event", "create", "Run", "--start", "tomorrow", "--duration", "45m", env=LONDON)
        assert created(cal)["duration"] == "PT45M"

    def test_attendees_with_invitations(self, invoke, cal):
        result = invoke(
            "event", "create", "Sync", "--start", "2026-11-05T09:00",
            "--attendee", "Bob <bob@example.com>, carol@example.com", "--send-invites", env=LONDON,
        )  # fmt: skip
        assert result.code == 0, result.err
        body = created(cal)
        assert body["organizerCalendarAddress"] == "mailto:alice@example.com"
        people = body["participants"]
        assert people["0"]["roles"] == {"owner": True, "attendee": True}
        assert people["0"]["name"] == "Alice"
        assert people["1"]["name"] == "Bob"
        assert people["1"]["calendarAddress"] == "mailto:bob@example.com"
        assert "name" not in people["2"]
        assert people["2"]["expectReply"] is True
        assert cal.last_set["sendSchedulingMessages"] is True
        assert "no invitations were sent" not in result.err

    def test_attendees_without_an_identity_and_without_invitations(self, invoke, cal):
        cal.identities = []
        result = invoke(
            "event", "create", "Sync", "--start", "2026-11-05T09:00",
            "--attendee", "bob@example.com", env=LONDON,
        )  # fmt: skip
        assert created(cal)["organizerCalendarAddress"] == "mailto:alice@example.com"
        assert "name" not in created(cal)["participants"]["0"]
        assert "no invitations were sent" in result.err

    def test_the_default_identity_is_the_organizer(self, invoke, cal):
        cal.identities.append(
            {"id": "pi2", "calendarAddress": "mailto:a@work.example", "isDefault": True}
        )
        invoke("event", "create", "Sync", "--start", "2026-11-05T09:00", "--attendee", "b@x.org")
        assert created(cal)["organizerCalendarAddress"] == "mailto:a@work.example"

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--duration", "1h", "--end", "+1h"], "--duration or --end, not both"),
            (["--end", "2026-11-01"], "--end must come after --start"),
            (["--duration", "soon"], "'soon' is not a duration"),
            (["--rrule", "COUNT=3"], "has no FREQ"),
            (["--attendee", "not an address"], "is not an email address"),
        ],
    )
    def test_refused(self, invoke, cal, args, message):
        result = invoke("event", "create", "X", "--start", "2026-11-05T09:00", *args, env=LONDON)
        assert result.code == 2
        assert message in result.err

    def test_no_calendars(self, invoke, cal):
        cal.calendars.clear()
        result = invoke("event", "create", "X", "--start", "2026-11-05T09:00", env=LONDON)
        assert result.code == 9
        assert "there are no calendars" in result.err

    def test_not_created(self, invoke, cal, server):
        server.respond(
            "CalendarEvent/set",
            {"accountId": "a", "notCreated": {"event": {"type": "invalidProperties"}}},
        )
        result = invoke("event", "create", "X", "--start", "2026-11-05T09:00", env=LONDON)
        assert result.code == 8
        assert "its id is" not in result.err


class TestImport:
    def test_a_file(self, invoke, cal, tmp_path):
        source = tmp_path / "events.ics"
        source.write_bytes(ICS)
        result = invoke("event", "import", str(source), "--calendar", "Work")
        assert result.code == 0, result.err
        assert "imported 2 event(s) into Work" in result.err
        first = cal.last_set["create"]["e1"]
        assert first["title"] == "Gig"
        assert first["calendarIds"] == {"c2": True}
        assert "id" not in first

    def test_stdin(self, make_context, cal):
        context = make_context()
        context.stdin = io.TextIOWrapper(io.BytesIO(ICS))
        assert run(["event", "import", "-"], context=context) == 0
        assert "imported 2 event(s) into Personal" in text_of(context.err.file)

    def test_stdin_as_text(self, invoke, cal):
        result = invoke("event", "import", "-", stdin=ICS.decode())
        assert result.code == 0, result.err

    def test_no_events(self, invoke, cal, tmp_path):
        source = tmp_path / "empty.ics"
        source.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        result = invoke("event", "import", str(source))
        assert result.code == 9
        assert "found no events" in result.err

    def test_unreadable(self, invoke, cal, tmp_path):
        result = invoke("event", "import", str(tmp_path / "missing.ics"))
        assert result.code == 2
        assert "cannot read" in result.err


class TestUpdate:
    def test_fields(self, invoke, cal):
        result = invoke(
            "event", "update", "e1", "--title", "Dentist (moved)", "--start", "2026-11-04T08:00",
            "--duration", "30m", "--tz", "Europe/Paris", "--location", "Clinic",
            "--description", "Again", "--status", "tentative", "--calendar", "Work",
            "--send-invites",
        )  # fmt: skip
        assert result.code == 0, result.err
        assert "updated Dentist" in result.err
        stored = cal.events["e1"]
        assert stored["title"] == "Dentist (moved)"
        assert stored["start"] == "2026-11-04T08:00:00"
        assert stored["duration"] == "PT30M"
        assert stored["timeZone"] == "Europe/Paris"
        assert stored["locations"] == {"1": {"name": "Clinic"}}
        assert stored["status"] == "tentative"
        assert stored["calendarIds"] == {"c2": True}
        assert cal.last_set["sendSchedulingMessages"] is True

    def test_nothing_to_change(self, invoke, cal):
        result = invoke("event", "update", "e1")
        assert result.code == 2
        assert "nothing to change" in result.err

    def test_an_occurrence_is_refused(self, invoke, cal):
        result = invoke("event", "update", "e2.2026-11-03T10:00:00", "--title", "X")
        assert result.code == 2
        assert "use e2 to change every occurrence" in result.err

    def test_this_occurrence(self, invoke, cal):
        occurrence = "e2.2026-11-03T10:00:00"
        result = invoke("event", "update", occurrence, "--this-occurrence", "--title", "Late")
        assert result.code == 0, result.err
        assert cal.events["e2"]["recurrenceOverrides"] == {"2026-11-03T10:00:00": {"title": "Late"}}
        result = invoke(
            "event", "update", occurrence, "--this-occurrence", "--start", "2026-11-03T11:00"
        )
        assert result.code == 0, result.err
        assert cal.last_set["update"] == {
            "e2": {
                "recurrenceOverrides/2026-11-03T10:00:00": {
                    "title": "Late",
                    "start": "2026-11-03T11:00:00",
                }
            }
        }

    def test_this_occurrence_cannot_change_calendar(self, invoke, cal):
        result = invoke(
            "event", "update", "e2.2026-11-03T10:00:00", "--this-occurrence", "--calendar", "c1"
        )
        assert result.code == 2
        assert "cannot move to another calendar" in result.err
