"""Creating and changing mailboxes, and emptying one."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.mailstore import email

from .conftest import Result, ScriptedPrompter

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("account")


@pytest.fixture(autouse=True)
def mail(store):
    store.add(
        email("e1", "Old spam", received="2025-01-01T09:00:00Z", mailboxes=("junk",)),
        email("e2", "New spam", received="2026-10-29T09:00:00Z", mailboxes=("junk",)),
    )
    return store


def paths(invoke: Callable[..., Result]) -> list[str]:
    return [line.split("\t")[0] for line in invoke("mailbox", "list").out.splitlines()]


def test_create_at_the_top_and_under_a_parent(invoke, store):
    assert invoke("mailbox", "create", "Receipts").code == 0
    result = invoke("mailbox", "create", "Archive/2026", "--role", "Important")
    assert "created Archive/2026" in result.err
    assert "Archive/2026" in paths(invoke)
    assert store.mailboxes["new2"]["role"] == "important"
    assert "Receipts" in paths(invoke)


def test_create_under_a_missing_parent(invoke):
    assert invoke("mailbox", "create", "Nope/x").code == 9


def test_rename(invoke):
    assert invoke("mailbox", "rename", "archive/2025", "Twenty-five").code == 0
    assert "Archive/Twenty-five" in paths(invoke)
    assert invoke("mailbox", "rename", "y25", "a/b").code == 2


def test_move_under_another_and_to_the_top(invoke):
    result = invoke("mailbox", "move", "y25", "trash")
    assert "moved 2025 to Trash" in result.err
    assert "Trash/2025" in paths(invoke)
    assert "to the top level" in invoke("mailbox", "move", "y25").err
    assert "2025" in paths(invoke)


def test_subscribe_and_unsubscribe(invoke, store):
    invoke("mailbox", "unsubscribe", "inbox")
    assert store.mailboxes["inbox"]["isSubscribed"] is False
    assert "subscribed to Inbox" in invoke("mailbox", "subscribe", "inbox").err


def test_delete_an_empty_mailbox(invoke, store):
    result = invoke("mailbox", "delete", "y25", "-y")
    assert "deleted Archive/2025" in result.err
    assert "y25" not in store.mailboxes


def test_delete_refuses_a_mailbox_with_mail_unless_asked(invoke, store):
    result = invoke("mailbox", "delete", "junk", prompter=ScriptedPrompter(confirms=[True]))
    assert result.code == 8
    assert "mailboxHasEmail" in result.err
    assert invoke("mailbox", "delete", "junk", "--with-mail", "-y").code == 0


def test_delete_needs_confirmation(invoke, store):
    assert invoke("mailbox", "delete", "y25").code == 1
    assert "y25" in store.mailboxes


def test_empty(invoke, store):
    result = invoke("mailbox", "empty", "junk", "-y")
    assert "deleted 2 message(s) in Junk" in result.err
    assert store.emails == {}


def test_empty_only_what_matches(invoke, store, monkeypatch):
    from jmap.cli.commands import mailbox_edit

    monkeypatch.setattr(mailbox_edit, "_EMPTY_PAGE", 1)
    invoke("mailbox", "empty", "junk", "--match", "before:2026-01-01", "-y")
    assert list(store.emails) == ["e2"]


def test_empty_nothing(invoke):
    result = invoke("mailbox", "empty", "trash")
    assert result.code == 0
    assert "has nothing to delete" in result.err
