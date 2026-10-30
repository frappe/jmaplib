"""``jmap calendar`` and reading events: ``event list``, ``show``, ``delete``, ``notifications``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.calendarstore import CalendarStore, event

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

LONDON = {"TZ": "Europe/London"}


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
            duration="PT15M",
            recurrenceRule={"frequency": "daily", "count": 3},
        ),
        event(
            "e3", "Holiday", "2026-11-04T00:00:00", duration="P1D", zone=None, showWithoutTime=True
        ),
    )
    return calendars


def rows(out: str) -> list[list[str]]:
    return [line.split("\t") for line in out.splitlines()]


class TestCalendar:
    def test_list(self, invoke, cal):
        result = invoke("calendar", "list")
        assert rows(result.out) == [
            ["Personal", "yes", "blue", "", "c1"],
            ["Work", "", "red", "Europe/London", "c2"],
        ]

    def test_list_opts_in_to_the_experimental_capability(self, invoke, cal):
        result = invoke("calendar", "list", "--json")
        assert [item["id"] for item in result.json()] == ["c1", "c2"]
        assert result.context.connected is None or result.context.connected.experimental

    def test_create(self, invoke, cal):
        result = invoke(
            "calendar",
            "create",
            "Trips",
            "--css-color",
            "green",
            "--description",
            "Away",
            "--tz",
            "Europe/Paris",
            "--default",
        )
        assert result.code == 0, result.err
        [created] = [item for item in cal.calendars.values() if item["name"] == "Trips"]
        assert created["name"] == "Trips"
        assert created["timeZone"] == "Europe/Paris"
        assert created["isDefault"]
        assert not cal.calendars["c1"]["isDefault"]

    def test_create_with_a_name_only(self, invoke, cal):
        assert invoke("calendar", "create", "Trips").code == 0
        [created] = [item for item in cal.calendars.values() if item["name"] == "Trips"]
        assert set(created) == {"id", "name", "isDefault"}
        assert "onSuccessSetIsDefault" not in cal.server.requests[-1]["methodCalls"][0][1]

    def test_rename_by_name_in_any_case(self, invoke, cal):
        result = invoke("calendar", "rename", "work", "Office")
        assert "renamed Work to Office" in result.err
        assert cal.calendars["c2"]["name"] == "Office"

    def test_delete_needs_yes_off_a_terminal(self, invoke, cal):
        result = invoke("calendar", "delete", "c2")
        assert result.code == 1
        assert "--yes" in result.err
        assert "c2" in cal.calendars

    def test_delete_with_events_refused(self, invoke, cal):
        result = invoke("calendar", "delete", "Work", "--yes")
        assert result.code == 8
        assert "calendarHasEvent" in result.err

    def test_delete_with_events(self, invoke, cal):
        result = invoke("calendar", "delete", "Work", "--yes", "--with-events")
        assert result.code == 0, result.err
        assert "c2" not in cal.calendars
        assert "e2" not in cal.events

    def test_not_found(self, invoke, cal):
        result = invoke("calendar", "rename", "Nope", "X")
        assert result.code == 9
        assert "no calendar 'Nope'" in result.err

    def test_ambiguous(self, invoke, cal):
        cal.calendars["c3"] = {"id": "c3", "name": "work"}
        result = invoke("calendar", "rename", "Work", "X")
        assert result.code == 2
        assert "2 calendars are called 'Work'" in result.err

    def test_unsupported_without_the_capability(self, invoke, server, account):
        result = invoke("calendar", "list")
        assert result.code == 7


class TestEventList:
    def test_a_week_from_a_day_each_occurrence_on_its_own_row(self, invoke, cal):
        result = invoke("event", "list", "--from", "2026-11-02", env=LONDON)
        assert result.code == 0, result.err
        assert [row[2] for row in rows(result.out)] == [
            "Standup",
            "Dentist",
            "Standup",
            "Holiday",
            "Standup",
        ]
        first = rows(result.out)[0]
        assert first[:2] == ["2026-11-02 10:00", "15m"]
        assert first[3] == "Work"
        assert rows(result.out)[3][:2] == ["2026-11-04", "1d"]
        [query] = cal.queries
        assert query["filter"] == {"after": "2026-11-02T00:00:00", "before": "2026-11-09T00:00:00"}
        assert query["expandRecurrences"] is True
        assert query["timeZone"] == "Europe/London"

    def test_shows_the_instant_in_the_zone_asked_for(self, invoke, cal):
        result = invoke(
            "event", "list", "--from", "2026-11-03", "--to", "+1d", "--tz", "America/New_York"
        )
        assert rows(result.out)[0][:3] == ["2026-11-03 04:00", "1h", "Dentist"]

    def test_no_expand(self, invoke, cal):
        result = invoke("event", "list", "--from", "2026-11-02", "--no-expand", env=LONDON)
        assert [row[2] for row in rows(result.out)] == ["Standup", "Dentist", "Holiday"]
        assert cal.queries[0]["expandRecurrences"] is False

    def test_a_window_wider_than_the_server_expands_is_split(self, invoke, cal):
        result = invoke("event", "list", "--from", "2026-10-01", "--to", "+70d", env=LONDON)
        assert result.code == 0, result.err
        spans = [(q["filter"]["after"], q["filter"]["before"]) for q in cal.queries]
        assert spans == [
            ("2026-10-01T00:00:00", "2026-10-31T00:00:00"),
            ("2026-10-31T00:00:00", "2026-11-30T00:00:00"),
            ("2026-11-30T00:00:00", "2026-12-10T00:00:00"),
        ]
        assert len(rows(result.out)) == 5

    def test_an_event_in_two_windows_is_listed_once(self, invoke, cal):
        cal.add(event("e4", "Long", "2026-10-30T22:00:00", duration="PT4H"))
        result = invoke("event", "list", "--from", "2026-10-01", "--to", "+40d", env=LONDON)
        assert [row[2] for row in rows(result.out)].count("Long") == 1

    def test_only_some_calendars(self, invoke, cal):
        result = invoke("event", "list", "--from", "2026-11-02", "--calendar", "Work", env=LONDON)
        assert {row[2] for row in rows(result.out)} == {"Standup"}
        assert cal.queries[0]["filter"]["inCalendars"] == ["c2"]

    def test_json_is_the_server_objects(self, invoke, cal):
        result = invoke(
            "event", "list", "--from", "2026-11-03", "--to", "+1d", "--json", env=LONDON
        )
        item = result.json()[0]
        assert item["id"] == "e1"
        assert item["utcStart"] == "2026-11-03T09:00:00Z"

    def test_without_a_limit_the_window_is_one_query(self, invoke, server, account):
        calendars = CalendarStore(server, expand_limit=None)
        invoke("event", "list", "--from", "2026-01-01", "--to", "+300d", env=LONDON)
        assert len(calendars.queries) == 1

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--from", "soon"], "'soon' is not a time"),
            (["--from", "2026-11-02", "--to", "2026-11-01"], "--to must come after --from"),
            (["--tz", "Mars/Olympus"], "'Mars/Olympus' is not a time zone"),
            (["--from", "2026-11-02T10:00+01:00"], "has an offset"),
        ],
    )
    def test_bad_times(self, invoke, cal, args, message):
        result = invoke("event", "list", *args, env=LONDON)
        assert result.code == 2
        assert message in result.err


class TestEventShow:
    def test_show(self, invoke, cal):
        cal.events["e1"].update(
            locations={"1": {"name": "Clinic"}},
            participants={
                "0": {"name": "Alice", "participationStatus": "accepted"},
                "1": {"email": "bob@example.com"},
            },
            alerts={"1": {"trigger": {"offset": "-PT15M"}}},
            description="Bring the form",
        )
        result = invoke("event", "show", "e1", env=LONDON)
        fields = dict(row[:2] for row in rows(result.out))
        assert fields["title"] == "Dentist"
        assert fields["start"] == "2026-11-03 09:00"
        assert fields["calendars"] == "Personal"
        assert fields["location"] == "Clinic"
        assert fields["participants"] == "Alice (accepted), bob@example.com (needs-action)"
        assert fields["alerts"] == "-PT15M"
        assert fields["repeats"] == ""

    def test_show_a_rule_and_a_floating_start(self, invoke, cal):
        cal.events["e2"].update(
            timeZone=None,
            recurrenceRule={
                "frequency": "weekly",
                "interval": 2,
                "byDay": [{"day": "mo"}],
                "count": 3,
            },
        )
        fields = dict(row[:2] for row in rows(invoke("event", "show", "e2", env=LONDON).out))
        assert fields["repeats"] == "weekly, every 2, on mo, times 3"
        assert fields["start"] == "2026-11-02 10:00"

    def test_show_an_occurrence(self, invoke, cal):
        result = invoke("event", "show", "e2.2026-11-03T10:00:00", "--json", env=LONDON)
        assert result.json()["baseEventId"] == "e2"

    def test_not_found(self, invoke, cal):
        result = invoke("event", "show", "nope", env=LONDON)
        assert result.code == 9


class TestEventDelete:
    def test_delete(self, invoke, cal):
        result = invoke("event", "delete", "e1", "--yes")
        assert "deleted Dentist" in result.err
        assert "e1" not in cal.events

    def test_its_own_base_id_is_not_an_occurrence(self, invoke, cal):
        """Stalwart sets ``baseEventId`` to the event's own id."""
        cal.events["e1"]["baseEventId"] = "e1"
        fields = dict(row[:2] for row in rows(invoke("event", "show", "e1", env=LONDON).out))
        assert fields["occurrence of"] == ""
        assert invoke("event", "delete", "e1", "--yes").code == 0
        assert "e1" not in cal.events

    def test_an_occurrence_is_refused_without_asking_for_it(self, invoke, cal):
        result = invoke("event", "delete", "e2.2026-11-03T10:00:00", "--yes")
        assert result.code == 2
        assert "one occurrence of the repeating event e2" in result.err
        assert "--this-occurrence" in result.err

    def test_this_occurrence_is_excluded(self, invoke, cal):
        result = invoke("event", "delete", "e2.2026-11-03T10:00:00", "--this-occurrence", "--yes")
        assert result.code == 0, result.err
        assert cal.events["e2"]["recurrenceOverrides"] == {
            "2026-11-03T10:00:00": {"excluded": True}
        }

    def test_this_occurrence_needs_an_occurrence(self, invoke, cal):
        result = invoke("event", "delete", "e1", "--this-occurrence", "--yes")
        assert result.code == 2
        assert "not one occurrence" in result.err


class TestNotifications:
    @pytest.fixture(autouse=True)
    def notes(self, cal):
        cal.notifications["n1"] = {
            "id": "n1",
            "created": "2026-10-30T08:00:00Z",
            "type": "updated",
            "changedBy": {"name": "Bob"},
            "event": {"title": "Review"},
            "comment": "moved",
        }
        cal.notifications["n2"] = {"id": "n2", "type": "created", "changedBy": {}}

    def test_list(self, invoke):
        result = invoke("event", "notifications")
        assert rows(result.out) == [
            ["2026-10-30T08:00:00Z", "updated", "Bob", "Review", "moved", "n1"],
            ["", "created", "", "", "", "n2"],
        ]

    def test_clear(self, invoke, cal):
        result = invoke("event", "notifications", "--clear")
        assert "cleared 2 notification(s)" in result.err
        assert cal.notifications == {}
