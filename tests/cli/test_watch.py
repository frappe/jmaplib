"""``jmap watch``, driven to its end by the fake server's stream ending.

``EventSourceClient.listen`` reconnects forever, so these tests swap it for
``events``, which reads one connection: the queued events, then the end.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from jmap.push import EventSourceClient
from tests.fake.mailstore import MailStore, email

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

ALERT = {
    "@type": "CalendarAlert",
    "accountId": "a",
    "calendarEventId": "ev1",
    "uid": "u1",
    "alertId": "al1",
}


@pytest.fixture(autouse=True)
def one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(EventSourceClient, "listen", EventSourceClient.events)


@pytest.fixture
def urls(server: FakeJMAPServer) -> list[str]:
    """The URL of every event-source connection."""
    seen: list[str] = []

    def spy(request: Any) -> None:
        if "/eventsource/" in request.url.path:
            seen.append(str(request.url))

    server.intercept = spy
    return seen


def alert(server: FakeJMAPServer) -> None:
    server.push_events.append(f"event: calendarAlert\ndata: {json.dumps(ALERT)}\n\n")


class TestChanges:
    def test_one_line_per_type_that_moved(self, invoke, server, account, urls):
        server.push_ping(30)
        server.push("a", {"Email": "e2", "Mailbox": "m2"}, event_id="1")
        result = invoke("watch")
        assert result.code == 0, result.err
        assert result.out.splitlines() == ["a\tEmail\te2", "a\tMailbox\tm2"]
        assert "types=%2A" in urls[0]
        assert "ping=30" in urls[0]

    def test_json_is_one_object_per_line(self, invoke, server, account):
        server.push("a", {"Email": "e2"})
        alert(server)
        lines = invoke("watch", "--json").out.splitlines()
        assert json.loads(lines[0]) == {"@type": "StateChange", "changed": {"a": {"Email": "e2"}}}
        assert json.loads(lines[1]) == ALERT

    def test_a_calendar_alert(self, invoke, server, account):
        alert(server)
        assert invoke("watch").out == "a\tCalendarAlert\tev1\n"

    def test_chosen_types(self, invoke, server, account, urls):
        invoke("watch", "--types", "Email,Mailbox", "--types", "Thread", "--ping", "0")
        assert "types=Email%2CMailbox%2CThread" in urls[0]

    def test_a_type_the_server_cannot_push(self, invoke, account):
        result = invoke("watch", "--types", "Bogus")
        assert result.code == 2
        assert "Bogus" in result.err


class TestExec:
    @pytest.fixture
    def runs(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append({"command": command, **kwargs})
            return subprocess.CompletedProcess(command, 3 if "fail" in command else 0)

        monkeypatch.setattr(subprocess, "run", fake)
        return calls

    def test_the_command_gets_the_event(self, invoke, server, account, runs):
        server.push("a", {"Email": "e2", "Mailbox": "m2"})
        result = invoke("watch", "--exec", "notify --all")
        assert result.out == ""
        [run] = runs
        assert run["command"] == ["notify", "--all"]
        assert run["env"]["JMAP_ACCOUNT"] == "work"
        assert run["env"]["JMAP_TYPES"] == "Email,Mailbox"
        assert json.loads(run["env"]["JMAP_STATES"]) == {"a": {"Email": "e2", "Mailbox": "m2"}}
        assert json.loads(run["input"])["@type"] == "StateChange"

    def test_a_calendar_alert(self, invoke, server, account, runs):
        alert(server)
        invoke("watch", "--exec", "notify")
        assert runs[0]["env"]["JMAP_TYPES"] == "CalendarAlert"
        assert runs[0]["env"]["JMAP_STATES"] == "{}"

    def test_a_failing_command_is_noted(self, invoke, server, account, runs):
        server.push("a", {"Email": "e2"})
        result = invoke("watch", "--exec", "fail")
        assert result.code == 0
        assert "fail exited 3" in result.err

    def test_a_command_that_cannot_run(self, invoke, server, account, monkeypatch):
        def missing(command: list[str], **_: Any) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(subprocess, "run", missing)
        server.push("a", {"Email": "e2"})
        result = invoke("watch", "--exec", "nothere")
        assert result.code == 2
        assert "cannot run nothere: No such file or directory" in result.err


class TestNewMail:
    @pytest.fixture
    def pages(self) -> dict[str, tuple[list[str], str, bool]]:
        """Email state -> (created ids, the next state, whether more follow)."""
        return {"s": (["e9"], "t0", True), "t0": (["e10"], "t1", False)}

    @pytest.fixture(autouse=True)
    def mail(self, mailer: MailStore, server: FakeJMAPServer, pages: dict[str, Any]) -> MailStore:
        mailer.add(
            email("e9", "Invoice 12", received="2026-10-30T09:00:00Z"),
            email("e10", "Hello", sender="carol@example.com", received="2026-10-30T09:05:00Z"),
        )

        def changes(args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
            created, new_state, more = pages[args["sinceState"]]
            return {
                "accountId": args["accountId"],
                "oldState": args["sinceState"],
                "newState": new_state,
                "hasMoreChanges": more,
                "created": created,
                "updated": [],
                "destroyed": [],
            }

        server.handle("Email/changes", changes)
        return mailer

    def test_one_line_per_new_message(self, invoke, server):
        server.push("a", {"Email": "t1"})
        server.push("a", {"Email": "t1", "Mailbox": "m9"})  # Email did not move again
        server.push("b", {"Email": "x"})  # another account
        server.push("a", {"Mailbox": "m9"})
        result = invoke("watch", "--new-mail")
        assert result.code == 0, result.err
        assert result.out.splitlines() == [
            "e9\t2026-10-30T09:00:00Z\tBob\tInvoice 12",
            "e10\t2026-10-30T09:05:00Z\tCarol\tHello",
        ]

    def test_json(self, invoke, server):
        server.push("a", {"Email": "t1"})
        lines = invoke("watch", "--new-mail", "--jsonl").out.splitlines()
        assert [json.loads(line)["subject"] for line in lines] == ["Invoice 12", "Hello"]

    def test_match(self, invoke, server):
        server.push("a", {"Email": "t1"})
        result = invoke("watch", "--new-mail", "--match", "subject:invoice")
        assert result.out.splitlines() == ["e9\t2026-10-30T09:00:00Z\tBob\tInvoice 12"]
        query = server.requests[-1]["methodCalls"][0][1]["filter"]
        assert query["conditions"] == [{"subject": "invoice"}, {"after": "2026-10-30T08:59:59Z"}]

    def test_match_with_nothing_new(self, invoke, server, pages):
        pages["s"] = ([], "t2", False)
        server.push("a", {"Email": "t2"})
        assert invoke("watch", "--new-mail", "--match", "subject:invoice").out == ""

    def test_an_alert_and_a_command(self, invoke, server, monkeypatch):
        ran: list[list[str]] = []

        def fake(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            ran.append(command)
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", fake)
        alert(server)
        result = invoke("watch", "--new-mail", "--exec", "beep")
        assert result.out == ""
        assert ran == [["beep"]]

    def test_email_is_always_watched(self, invoke, server, urls):
        invoke("watch", "--new-mail", "--types", "Mailbox")
        assert "types=Mailbox%2CEmail" in urls[0]

    def test_a_forgotten_state_starts_again(self, invoke, server):
        server.fail("Email/changes", "cannotCalculateChanges")
        server.push("a", {"Email": "t1"})
        result = invoke("watch", "--new-mail")
        assert result.code == 0, result.err
        assert result.out == ""

    def test_another_failure_ends_it(self, invoke, server):
        server.fail("Email/changes", "serverFail")
        server.push("a", {"Email": "t1"})
        assert invoke("watch", "--new-mail").code == 6

    def test_match_needs_new_mail(self, invoke):
        assert invoke("watch", "--match", "from:bob").code == 2
