"""``jmap freebusy``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.calendarstore import CalendarStore

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

LONDON = {"TZ": "Europe/London"}


@pytest.fixture
def cal(server: FakeJMAPServer, account: str) -> CalendarStore:
    calendars = CalendarStore(server)
    calendars.busy = [
        {"utcStart": "2026-11-02T09:00:00Z", "utcEnd": "2026-11-02T10:00:00Z"},
        {
            "utcStart": "2026-11-03T14:00:00Z",
            "busyStatus": "tentative",
            "event": {"title": "Review"},
        },
    ]
    return calendars


def test_by_address(invoke, cal):
    result = invoke("freebusy", "bob@example.com", "--from", "2026-11-02", env=LONDON)
    assert result.code == 0, result.err
    assert [line.split("\t") for line in result.out.splitlines()] == [
        ["2026-11-02 09:00", "2026-11-02 10:00", "unavailable"],
        ["2026-11-03 14:00", "", "tentative"],
    ]
    assert cal.last_availability["id"] == "p-bob"
    assert cal.last_availability["utcStart"] == "2026-11-02T00:00:00Z"
    assert cal.last_availability["utcEnd"] == "2026-11-09T00:00:00Z"
    assert cal.last_availability["showDetails"] is False


def test_by_id_with_details_in_another_zone(invoke, cal):
    result = invoke(
        "freebusy", "p-room", "--from", "2026-11-02", "--to", "+1d", "--details",
        "--tz", "Asia/Tokyo",
    )  # fmt: skip
    rows = [line.split("\t") for line in result.out.splitlines()]
    assert rows[0] == ["2026-11-02 18:00", "2026-11-02 19:00", "unavailable", ""]
    assert rows[1][3] == "Review"
    assert cal.last_availability["utcStart"] == "2026-11-01T15:00:00Z"
    assert cal.last_availability["showDetails"] is True


def test_unknown_address(invoke, cal):
    result = invoke("freebusy", "nobody@example.com", env=LONDON)
    assert result.code == 9
    assert "no principal has the address nobody@example.com" in result.err


def test_an_address_two_principals_share(invoke, cal):
    cal.principals["p-bob2"] = "bob@example.com"
    result = invoke("freebusy", "bob@example.com", env=LONDON)
    assert result.code == 2
    assert "2 principals have the address" in result.err


def test_a_window_wider_than_the_server_allows(invoke, cal):
    result = invoke("freebusy", "p-bob", "--to", "+70d", env=LONDON)
    assert result.code == 2
    assert "maxAvailabilityDuration" in result.err
