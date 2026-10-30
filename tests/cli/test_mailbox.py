"""``jmap mailbox list`` and ``show``."""

from __future__ import annotations

import pytest

from .conftest import MAILBOXES

pytestmark = pytest.mark.usefixtures("account")


class TestList:
    def test_plain_paths_in_tree_order(self, invoke):
        assert invoke("mailbox", "list").out == (
            "Inbox\tinbox\t1\t3\tm1\nArchive\tarchive\t0\t0\tm2\nArchive/2025\t\t0\t9\tm3\n"
        )

    def test_one_request(self, invoke, server):
        invoke("mailbox", "list")
        [request] = server.requests
        [[name, args, _]] = request["methodCalls"]
        assert name == "Mailbox/get"
        assert args["ids"] is None
        assert "myRights" in args["properties"]

    def test_tree(self, invoke):
        lines = invoke("mailbox", "list", "--tree").out.splitlines()
        assert lines[2].startswith("  2025\t")

    def test_subscribed(self, invoke):
        assert [
            line.split("\t")[-1]
            for line in invoke("mailbox", "list", "--subscribed").out.splitlines()
        ] == ["m1", "m2"]

    def test_json_is_wire_objects(self, invoke):
        assert invoke("mailbox", "list", "--json").json() == MAILBOXES

    def test_table(self, invoke):
        result = invoke("mailbox", "list", terminal=True)
        assert "UNREAD" in result.out
        assert "Archive/2025" in result.out


class TestShow:
    def test_by_path(self, invoke):
        result = invoke("mailbox", "show", "archive/2025")
        assert result.code == 0
        assert "path\tArchive/2025" in result.out
        assert "parent\tArchive" in result.out
        assert "subscribed\tno" in result.out

    def test_rights(self, invoke):
        assert "rights\tReadItems\n" in invoke("mailbox", "show", "inbox").out

    def test_json(self, invoke):
        assert invoke("mailbox", "show", "m1", "--json").json() == MAILBOXES[0]

    def test_missing(self, invoke):
        result = invoke("mailbox", "show", "Receipts")
        assert result.code == 9
        assert "no mailbox 'Receipts'" in result.err

    def test_gone_since_the_listing(self, invoke, server):
        listing = {"accountId": "a", "state": "s", "list": MAILBOXES, "notFound": []}
        answers = [listing, {"accountId": "a", "state": "s", "list": [], "notFound": ["m1"]}]
        server.handle("Mailbox/get", lambda args, _server: answers.pop(0))
        assert invoke("mailbox", "show", "inbox", "--json").json()["id"] == "m1"
