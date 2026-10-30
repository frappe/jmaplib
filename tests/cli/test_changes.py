"""``jmap changes TYPE``: the current state, the changes since one, and --fetch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

#: sinceState -> one page of Mailbox/changes.
PAGES: dict[str, dict[str, Any]] = {
    "s1": {"newState": "s2", "hasMoreChanges": True, "created": ["m3"], "updated": []},
    "s2": {"newState": "s3", "hasMoreChanges": False, "created": [], "updated": ["m1"]},
    "s3": {"newState": "s3", "hasMoreChanges": False, "created": [], "updated": []},
}


@pytest.fixture(autouse=True)
def changes(server: FakeJMAPServer, account: str) -> None:
    def answer(args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        page = PAGES[args["sinceState"]]
        destroyed = ["m7"] if args["sinceState"] == "s2" else []
        return {
            "accountId": args["accountId"],
            "oldState": args["sinceState"],
            "destroyed": destroyed,
            **page,
        }

    server.handle("Mailbox/changes", answer)


def test_the_current_state(invoke):
    assert invoke("changes", "Mailbox").out == "s1\n"
    assert invoke("changes", "Mailbox", "--json").json()["state"] == "s1"


def test_every_page_is_followed(invoke, server):
    result = invoke("changes", "Mailbox", "--since", "s1", "--max", "1")
    assert result.out.splitlines() == ["created\tm3", "updated\tm1", "destroyed\tm7"]
    assert "new state: s3" in result.err
    assert server.requests[0]["methodCalls"][0][1]["maxChanges"] == 1


def test_json_is_one_merged_response(invoke):
    merged = invoke("changes", "Mailbox", "--since", "s1", "--json").json()
    assert merged == {
        "accountId": "a",
        "oldState": "s1",
        "newState": "s3",
        "hasMoreChanges": False,
        "created": ["m3"],
        "updated": ["m1"],
        "destroyed": ["m7"],
    }


def test_fetch(invoke):
    result = invoke("changes", "Mailbox", "--since", "s1", "--fetch")
    rows = [line.split("\t") for line in result.out.splitlines()]
    assert rows == [["created", "m3", "2025"], ["updated", "m1", "Inbox"], ["destroyed", "m7", ""]]


def test_fetch_as_json(invoke):
    result = invoke("changes", "Mailbox", "--since", "s2", "--fetch", "--json")
    assert [item["id"] for item in result.json()] == ["m1"]
    assert "destroyed: m7" in result.err


def test_fetch_with_nothing_changed(invoke):
    result = invoke("changes", "Mailbox", "--since", "s3", "--fetch", "--json")
    assert result.json() == []
    assert "destroyed" not in result.err


def test_a_dead_state(invoke, server):
    server.fail("Mailbox/changes", "cannotCalculateChanges")
    result = invoke("changes", "Mailbox", "--since", "s0")
    assert result.code == 10
    assert "re-download" in result.err


def test_a_type_without_changes(invoke):
    assert invoke("changes", "Nothing", "--since", "s1").code == 7
