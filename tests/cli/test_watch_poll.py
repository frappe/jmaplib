"""``jmap watch --poll`` and the note for an event source that sends nothing.

Polling never ends by itself, so ``time.sleep`` between checks is replaced:
each call moves the fake server's states on, and the last one stops the run
the way Ctrl-C does.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.commands import watch_sources
from jmap.cli.commands.watch_sources import SilenceNote, poll
from jmap.models.push import StateChange
from tests.fake.mailstore import MailStore, email

if TYPE_CHECKING:
    from collections.abc import Callable

    from jmap.testing import FakeJMAPServer

pytestmark = pytest.mark.usefixtures("account", "mailer")


@pytest.fixture
def states(server: FakeJMAPServer, mailer: MailStore) -> dict[str, str]:
    """The state each polled ``Foo/get`` answers with; tests change it."""
    current = {"Mailbox": "m0", "Email": "e0", "Thread": "t0"}
    for name in current:
        real = server.handlers[f"{name}/get"]

        def answer(
            args: dict[str, Any],
            srv: FakeJMAPServer,
            name: str = name,
            real: Callable[..., Any] = real,
        ) -> Any:
            return {**real(args, srv), "state": current[name]}

        server.handle(f"{name}/get", answer)
    return current


def stepping(monkeypatch: pytest.MonkeyPatch, *steps: Callable[[], None]) -> list[float]:
    """Run one step per sleep, then stop as Ctrl-C would."""
    slept: list[float] = []
    queue = list(steps)

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if not queue:
            raise KeyboardInterrupt
        queue.pop(0)()

    monkeypatch.setattr(time, "sleep", sleep)
    return slept


class TestPoll:
    def test_a_change_per_moved_state(self, invoke, states, monkeypatch, server):
        slept = stepping(
            monkeypatch,
            lambda: states.update(Email="e1", Thread="t1"),
            lambda: None,
            lambda: states.update(Mailbox="m1"),
        )
        result = invoke("watch", "--poll", "7")
        assert result.code == 130
        assert result.out.splitlines() == ["a\tEmail\te1", "a\tThread\tt1", "a\tMailbox\tm1"]
        assert slept == [7, 7, 7, 7]
        names = [call[0] for call in server.requests[-1]["methodCalls"]]
        assert names == [
            "Mailbox/get",
            "Email/get",
            "Thread/get",
            "EmailSubmission/get",
            "Identity/get",
        ]

    def test_chosen_types(self, invoke, states, monkeypatch, server):
        stepping(monkeypatch, lambda: states.update(Email="e1", Mailbox="m1"))
        result = invoke("watch", "--poll", "5", "--types", "Email")
        assert result.out.splitlines() == ["a\tEmail\te1"]
        assert [call[0] for call in server.requests[-1]["methodCalls"]] == ["Email/get"]

    def test_a_type_the_server_lacks(self, invoke):
        result = invoke("watch", "--poll", "5", "--types", "Email,CalendarEvent")
        assert result.code == 2
        assert "no CalendarEvent/get to poll" in result.err

    def test_new_mail(self, invoke, states, monkeypatch, server, mailer):
        mailer.add(email("e9", "Invoice", received="2026-10-30T09:00:00Z"))
        server.handle(
            "Email/changes",
            lambda args, _srv: {
                "accountId": "a",
                "oldState": args["sinceState"],
                "newState": "e1",
                "hasMoreChanges": False,
                "created": ["e9"],
                "updated": [],
                "destroyed": [],
            },
        )
        stepping(monkeypatch, lambda: states.update(Email="e1"))
        result = invoke("watch", "--poll", "5", "--new-mail")
        assert result.out.splitlines() == ["e9\t2026-10-30T09:00:00Z\tBob\tInvoice"]

    def test_each_account_its_own(self, client_with_two_accounts):
        client, sleep = client_with_two_accounts
        changes = poll(client, ["Email", "Mailbox"], 1)
        assert next(changes) == StateChange(changed={"a": {"Email": "e1"}, "b": {"Mailbox": "m1"}})
        assert sleep == [1]

    @pytest.fixture
    def client_with_two_accounts(self, make_context, server, states, monkeypatch):
        """Email in account a, Mailbox in account b, both moving after one sleep."""
        real = server.handlers["Mailbox/get"]
        server.handle("Mailbox/get", lambda args, srv: {**real(args, srv), "accountId": "b"})
        slept = stepping(monkeypatch, lambda: states.update(Email="e1", Mailbox="m1"))
        return make_context().client(), slept


class TestSilenceNote:
    def test_said_once_when_nothing_arrives(self, make_context, monkeypatch):
        monkeypatch.setattr(watch_sources, "PING_TIMEOUT_SLACK", -29.99)
        context = make_context()
        said = threading.Event()
        monkeypatch.setattr(type(context.renderer), "note", lambda _self, text: said.set())
        with SilenceNote(context, 1):
            assert said.wait(5)

    def test_not_after_something_arrived(self, make_context, monkeypatch):
        context = make_context()
        with SilenceNote(context, 1) as note:
            note.heard()
            note._timer.join(1)
            assert not note._timer.is_alive()
        assert context.err.file.getvalue() == ""

    def test_not_without_pings(self, make_context):
        with SilenceNote(make_context(), 0) as note:
            assert not note._timer.is_alive()

    def test_the_message(self, make_context):
        context = make_context()
        SilenceNote._say(context, 40)
        assert "sent nothing in 40s, not even a ping" in context.err.file.getvalue()
        assert "jmap watch --poll 30" in context.err.file.getvalue()
