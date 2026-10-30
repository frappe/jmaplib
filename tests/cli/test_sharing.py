"""``jmap principal`` and ``jmap share`` against a store that applies patches by pointer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.fake.sharestore import (
    CALENDARS_URN,
    OWNER_URN,
    PRINCIPALS_URN,
    ShareStore,
    share_server,
)

from .conftest import ScriptedPrompter

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@pytest.fixture(autouse=True)
def shares(server: FakeJMAPServer, account: str) -> ShareStore:
    return share_server(server)


def rows(text: str) -> list[list[str]]:
    return [line.split("\t") for line in text.splitlines()]


def last_call(server: FakeJMAPServer, method: str) -> dict[str, Any]:
    return next(
        args
        for body in reversed(server.requests)
        for name, args, _ in body["methodCalls"]
        if name == method
    )


class TestPrincipal:
    def test_list(self, invoke):
        result = invoke("principal", "list")
        assert [row[0] for row in rows(result.out)] == ["alice", "bob", "carol", "team"]

    def test_list_matching_text_and_type(self, invoke, server):
        result = invoke("principal", "list", "bob", "--type", "group")
        assert [row[2] for row in rows(result.out)] == ["Team Bob"]
        assert last_call(server, "Principal/query")["filter"] == {"text": "bob", "type": "group"}

    def test_list_sends_no_filter_when_none_is_given(self, invoke, server):
        invoke("principal", "list")
        assert "filter" not in last_call(server, "Principal/query")

    def test_list_refuses_an_unknown_type(self, invoke):
        result = invoke("principal", "list", "--type", "robot")
        assert result.code == 2
        assert "group" in result.err

    def test_show_by_id(self, invoke):
        result = invoke("principal", "show", "bob", "--json")
        assert result.json()["email"] == "bob@example.com"

    def test_show_by_address_or_name(self, invoke, server):
        assert invoke("principal", "show", "CAROL@example.com", "--json").json()["id"] == "carol"
        assert last_call(server, "Principal/query")["filter"] == {"email": "CAROL@example.com"}
        assert invoke("principal", "show", "team bob", "--json").json()["id"] == "team"

    def test_show_a_single_partial_match(self, invoke):
        assert invoke("principal", "show", "aro", "--json").json()["id"] == "carol"

    def test_show_an_ambiguous_name(self, invoke):
        result = invoke("principal", "show", "example")
        assert result.code == 2
        assert "Bob <bob@example.com>" in result.err

    def test_show_nobody(self, invoke):
        result = invoke("principal", "show", "zed")
        assert result.code == 9
        assert "principal list" in result.err

    def test_show_lists_capabilities_and_accounts(self, invoke, shares):
        shares.principals["bob"]["capabilities"] = {CALENDARS_URN: {}}
        shares.principals["bob"]["accounts"] = {"b": {}}
        result = invoke("principal", "show", "bob")
        assert ["capabilities", CALENDARS_URN] in rows(result.out)
        assert ["accounts", "b"] in rows(result.out)

    def test_me(self, invoke):
        result = invoke("principal", "me")
        assert ["name", "Alice"] in rows(result.out)

    def test_me_when_the_server_names_nobody(self, invoke, server):
        server.accounts["a"]["accountCapabilities"][PRINCIPALS_URN] = {}
        result = invoke("principal", "me")
        assert result.code == 9

    def test_me_when_there_is_no_principal_account(self, invoke, server):
        del server.primary_accounts[PRINCIPALS_URN]
        del server.primary_accounts["urn:ietf:params:jmap:core"]
        server.accounts["b"] = {"name": "other", "isPersonal": False, "isReadOnly": True}
        server.primary_accounts["urn:ietf:params:jmap:mail"] = "b"
        assert invoke("principal", "me").code == 9

    def test_me_when_my_principal_is_gone(self, invoke, shares):
        del shares.principals["alice"]
        result = invoke("principal", "me")
        assert result.code == 9
        assert "'alice'" in result.err

    def test_without_principals(self, invoke, server):
        del server.capabilities[PRINCIPALS_URN]
        del server.accounts["a"]["accountCapabilities"]
        assert invoke("principal", "list").code == 7


class TestGrant:
    def test_a_preset_patches_one_principal(self, invoke, shares, server):
        result = invoke("share", "grant", "mailbox:Team", "carol@example.com", "--write")
        assert result.code == 0, result.err
        team = shares.objects["Mailbox"]["m2"]
        assert set(team["shareWith"]) == {"bob", "carol"}
        assert team["shareWith"]["carol"]["mayAddItems"] is True
        assert team["shareWith"]["carol"]["mayDelete"] is False
        assert list(last_call(server, "Mailbox/set")["update"]["m2"]) == ["shareWith/carol"]
        assert "now has mayAddItems" in result.err

    def test_read_and_admin(self, invoke, shares):
        invoke("share", "grant", "addressbook:b1", "bob", "--read")
        assert shares.objects["AddressBook"]["b1"]["shareWith"]["bob"] == {
            "mayRead": True,
            "mayWrite": False,
            "mayShare": False,
            "mayDelete": False,
        }
        invoke("share", "grant", "file:f1", "bob", "--admin")
        assert all(shares.objects["FileNode"]["f1"]["shareWith"]["bob"].values())

    def test_named_rights(self, invoke, shares):
        result = invoke("share", "grant", "calendar:c1", "bob", "--rights", "readFreeBusy,mayRSVP")
        assert result.code == 0, result.err
        held = shares.objects["Calendar"]["c1"]["shareWith"]["bob"]
        assert {name for name, value in held.items() if value} == {"mayReadFreeBusy", "mayRSVP"}

    def test_a_right_of_the_servers_own(self, invoke, shares):
        invoke("share", "grant", "mailbox:Team", "bob", "--rights", "mayAdminister")
        assert shares.objects["Mailbox"]["m2"]["shareWith"]["bob"]["mayAdminister"] is True

    def test_an_unknown_right(self, invoke):
        result = invoke("share", "grant", "mailbox:Team", "bob", "--rights", "fly")
        assert result.code == 2
        assert "mayReadItems" in result.err

    def test_no_rights_given(self, invoke):
        result = invoke("share", "grant", "mailbox:Team", "bob", "--rights", "")
        assert result.code == 0
        assert "no rights" in result.err

    @pytest.mark.parametrize("flags", [(), ("--read", "--write")])
    def test_exactly_one_level(self, invoke, flags):
        assert invoke("share", "grant", "mailbox:Team", "bob", *flags).code == 2

    def test_the_owner_is_refused(self, invoke, server):
        result = invoke("share", "grant", "mailbox:Team", "alice", "--read")
        assert result.code == 2
        assert "owns" in result.err
        assert all(
            name != "Mailbox/set" for body in server.requests for name, *_ in body["methodCalls"]
        )

    def test_an_unknown_object(self, invoke):
        result = invoke("share", "grant", "calendar:nope", "bob", "--read")
        assert result.code == 8
        assert "notFound" in result.err

    def test_json(self, invoke):
        result = invoke("share", "grant", "mailbox:Team", "bob", "--read", "--json")
        assert result.json()["updated"] == {"m2": None}

    def test_a_principal_by_id_without_principals(self, invoke, server, shares):
        del server.capabilities[PRINCIPALS_URN]
        server.accounts["a"]["accountCapabilities"] = {}
        assert invoke("share", "grant", "addressbook:b1", "dave", "--read").code == 0
        assert "dave" in shares.objects["AddressBook"]["b1"]["shareWith"]

    def test_principals_in_another_account(self, invoke, server):
        server.accounts["p"] = {"name": "directory", "isPersonal": False, "isReadOnly": True}
        owner = server.accounts["a"]["accountCapabilities"][OWNER_URN]
        owner["accountIdForPrincipal"] = "p"
        invoke("share", "grant", "mailbox:Team", "carol", "--read")
        assert last_call(server, "Principal/get")["accountId"] == "p"

    @pytest.mark.parametrize("target", ["mailbox", "drawer:x", "mailbox:"])
    def test_a_bad_object(self, invoke, target):
        assert invoke("share", "grant", target, "bob", "--read").code == 2


class TestRevokeAndUnshare:
    def test_revoke_leaves_the_others(self, invoke, shares):
        invoke("share", "grant", "mailbox:Team", "carol", "--read")
        result = invoke("share", "revoke", "mailbox:Team", "bob")
        assert result.code == 0, result.err
        assert set(shares.objects["Mailbox"]["m2"]["shareWith"]) == {"carol"}

    def test_unshare_confirms(self, invoke, shares):
        assert invoke("share", "unshare", "mailbox:Team").code == 1
        assert shares.objects["Mailbox"]["m2"]["shareWith"]
        prompter = ScriptedPrompter(confirms=[True])
        assert invoke("share", "unshare", "mailbox:Team", prompter=prompter).code == 0
        assert shares.objects["Mailbox"]["m2"]["shareWith"] is None

    def test_subscribe_and_unsubscribe(self, invoke, shares):
        assert invoke("share", "unsubscribe", "calendar:c1").code == 0
        assert shares.objects["Calendar"]["c1"]["isSubscribed"] is False
        result = invoke("share", "subscribe", "calendar:c1")
        assert "subscribed to calendar:c1" in result.err
        assert shares.objects["Calendar"]["c1"]["isSubscribed"] is True


class TestList:
    def test_shared_mailboxes(self, invoke):
        result = invoke("share", "list")
        assert rows(result.out) == [["m2", "Team", "Bob <bob@example.com>: ReadItems, SetSeen"]]

    def test_a_kind(self, invoke):
        result = invoke("share", "list", "addressbook")
        assert rows(result.out) == [["b1", "Friends", "Carol <carol@example.com>: none"]]

    def test_a_kind_as_json(self, invoke, server):
        result = invoke("share", "list", "--json")
        assert [item["id"] for item in result.json()] == ["m2"]
        assert all(
            name != "Principal/get" for body in server.requests for name, *_ in body["methodCalls"]
        )

    def test_one_object(self, invoke):
        result = invoke("share", "list", "mailbox:Team")
        assert rows(result.out) == [["bob", "Bob <bob@example.com>", "ReadItems, SetSeen"]]

    def test_one_object_as_json(self, invoke):
        result = invoke("share", "list", "mailbox:Team", "--json")
        assert result.json()["shareWith"] == {"bob": {"mayReadItems": True, "maySetSeen": True}}

    def test_one_object_shared_with_nobody(self, invoke, server):
        result = invoke("share", "list", "file:f1")
        assert result.out == ""
        assert "docs is not shared" in result.err

    def test_a_principal_with_no_address(self, invoke):
        invoke("share", "grant", "calendar:c1", "team", "--read")
        result = invoke("share", "list", "calendar")
        assert rows(result.out) == [["c1", "Work", "Team Bob: ReadFreeBusy, ReadItems"]]

    def test_a_missing_object(self, invoke):
        assert invoke("share", "list", "calendar:nope").code == 9

    def test_names_without_principals(self, invoke, server):
        del server.capabilities[PRINCIPALS_URN]
        server.accounts["a"]["accountCapabilities"] = {}
        result = invoke("share", "list", "mailbox:Team")
        assert rows(result.out) == [["bob", "", "ReadItems, SetSeen"]]


class TestNotifications:
    def test_newest_first(self, invoke):
        result = invoke("share", "notifications")
        assert rows(result.out) == [
            ["2026-10-03T09:00:00Z", "", "AddressBook", "Carol's contacts", "revoked"],
            [
                "2026-10-02T09:00:00Z",
                "carol@example.com",
                "Calendar",
                "Carol's calendar",
                "+WriteAll -ReadItems",
            ],
            ["2026-10-01T09:00:00Z", "Bob", "Mailbox", "Bob's inbox", "granted ReadItems, SetSeen"],
        ]

    def test_clear(self, invoke, shares):
        assert invoke("share", "notifications", "--clear").code == 1
        result = invoke("share", "notifications", "--clear", "--yes")
        assert result.code == 0, result.err
        assert "deleted 3 notification(s)" in result.err
        assert not shares.notifications
