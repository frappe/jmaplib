"""Filing and flagging: moves are patches, keywords are patches, failures exit 8."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.fake.mailstore import MailStore, email

from .conftest import ScriptedPrompter

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

pytestmark = pytest.mark.usefixtures("account")


@pytest.fixture(autouse=True)
def mail(store):
    store.add(
        email("e1", "One", received="2026-10-28T09:00:00Z", mailboxes=("inbox", "y25")),
        email("e2", "Two", received="2026-10-29T09:00:00Z"),
        email("e3", "Three", received="2026-10-30T09:00:00Z", mailboxes=("trash",)),
    )
    return store


def boxes(store: MailStore, email_id: str) -> set[str]:
    return set(store.emails[email_id]["mailboxIds"])


def patches(server: FakeJMAPServer) -> dict[str, Any]:
    update: dict[str, Any] = server.requests[-1]["methodCalls"][0][1]["update"]
    return update


class TestMove:
    def test_from_the_listed_mailbox_keeps_other_filing(self, invoke, store, server):
        invoke("ls")
        result = invoke("move", "2", "archive")
        assert result.code == 0, result.err
        assert "moved 1 message(s) to Archive" in result.err
        assert boxes(store, "e1") == {"arch", "y25"}
        assert patches(server) == {"e1": {"mailboxIds/arch": True, "mailboxIds/inbox": None}}

    def test_by_id_with_from(self, invoke, store):
        assert invoke("mail", "move", "e1", "trash", "--from", "y25").code == 0
        assert boxes(store, "e1") == {"inbox", "trash"}

    def test_by_id_without_a_source_replaces_mailboxes(self, invoke, store):
        result = invoke("move", "e1", "archive")
        assert "source mailbox is unknown" in result.err
        assert boxes(store, "e1") == {"arch"}

    def test_into_the_mailbox_it_was_listed_from(self, invoke, server):
        invoke("ls")
        invoke("move", "1", "inbox")
        assert patches(server) == {"e2": {}}

    def test_json_prints_the_set_response(self, invoke):
        assert invoke("move", "e1", "archive", "--json").json()["updated"] == {"e1": None}

    def test_a_refused_message_exits_8(self, invoke):
        result = invoke("move", "e1", "gone", "archive")
        assert result.code == 8
        assert "moved 1 message(s)" in result.err
        assert "gone: notFound (gone)" in result.err

    def test_nothing_changed_at_all(self, invoke):
        result = invoke("move", "gone", "archive")
        assert result.code == 8
        assert "moved" not in result.err


class TestCopy:
    def test_adds_only(self, invoke, store):
        assert invoke("mail", "copy", "e2", "archive").code == 0
        assert boxes(store, "e2") == {"inbox", "arch"}


class TestArchive:
    def test_out_of_the_inbox_by_default(self, invoke, store):
        assert invoke("archive", "e1").code == 0
        assert boxes(store, "e1") == {"arch", "y25"}

    def test_out_of_the_listed_mailbox(self, invoke, store):
        invoke("ls", "--in", "archive/2025")
        invoke("archive", "1")
        assert boxes(store, "e1") == {"inbox", "arch"}

    def test_without_an_inbox(self, invoke, store):
        del store.mailboxes["inbox"]
        invoke("archive", "e2")
        assert boxes(store, "e2") == {"arch"}

    def test_without_an_archive(self, invoke, store):
        del store.mailboxes["arch"]
        result = invoke("archive", "e1")
        assert result.code == 9
        assert "no archive mailbox" in result.err


class TestDelete:
    def test_to_trash(self, invoke, store):
        invoke("ls")
        assert invoke("delete", "1").code == 0
        assert boxes(store, "e2") == {"trash"}

    def test_already_in_trash(self, invoke):
        invoke("ls", "--in", "trash")
        result = invoke("delete", "1")
        assert result.code == 2
        assert "already in Trash" in result.err

    def test_permanently_after_confirming(self, invoke, store):
        result = invoke("delete", "e3", "--permanent", prompter=ScriptedPrompter(confirms=[True]))
        assert "deleted 1 message(s) permanently" in result.err
        assert "e3" not in store.emails

    def test_permanently_needs_a_yes(self, invoke, store):
        assert invoke("delete", "e3", "--permanent").code == 1
        assert invoke("delete", "e3", "--permanent", "-y").code == 0

    def test_without_trash(self, invoke, store):
        del store.mailboxes["trash"]
        assert invoke("delete", "e1").code == 9


class TestKeywords:
    @pytest.mark.parametrize(
        ("verb", "patch"),
        [
            ("flag", {"keywords/$flagged": True}),
            ("unflag", {"keywords/$flagged": None}),
            ("seen", {"keywords/$seen": True}),
            ("unseen", {"keywords/$seen": None}),
        ],
    )
    def test_the_daily_verbs(self, invoke, server, verb, patch):
        assert invoke(verb, "e1", "e2").code == 0
        assert patches(server) == {"e1": patch, "e2": patch}

    def test_junk_moves_to_junk(self, invoke, store):
        invoke("ls")
        assert invoke("mail", "junk", "1").code == 0
        assert boxes(store, "e2") == {"junk"}
        assert store.emails["e2"]["keywords"] == {"$junk": True}

    def test_notjunk_moves_to_the_inbox(self, invoke, store):
        invoke("ls", "--in", "trash")
        invoke("mail", "notjunk", "1")
        assert boxes(store, "e3") == {"inbox"}
        assert store.emails["e3"]["keywords"] == {"$notjunk": True}

    def test_junk_without_a_junk_mailbox_only_marks(self, invoke, store):
        del store.mailboxes["junk"]
        invoke("mail", "junk", "e2")
        assert boxes(store, "e2") == {"inbox"}

    def test_any_keyword(self, invoke, server):
        invoke("mail", "keyword", "e1", "--add", "Work", "--add", "flagged", "--remove", "seen")
        assert patches(server) == {
            "e1": {"keywords/work": True, "keywords/$flagged": True, "keywords/$seen": None}
        }

    def test_keyword_needs_a_change(self, invoke):
        assert invoke("mail", "keyword", "e1").code == 2

    def test_a_bad_keyword(self, invoke):
        result = invoke("mail", "keyword", "e1", "--add", "a(b")
        assert result.code == 2
        assert "keyword charset" in result.err
