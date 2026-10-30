"""The ``jmap`` command against a real server: contacts, calendars, files and sharing.

The same people as ``test_live_cli.py``. Calendars and files are experimental
capabilities, so the library client that decides the skips opts in to them, as
the command does by itself.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.cli_people import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    Person,
    requires_method,
    requires_second_account,
    requires_server,
)
from tests.integration.conftest import connect

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jmap.client import JMAPClient

pytestmark = [pytest.mark.integration, requires_server]


@pytest.fixture(scope="module")
def alice(tmp_path_factory: pytest.TempPathFactory) -> Person:
    person = Person(tmp_path_factory.mktemp("alice"), ALICE, ALICE_PASSWORD)
    person.add_account()
    return person


@pytest.fixture(scope="module")
def server() -> Iterator[JMAPClient]:
    """A library client for alice, opted in to the experimental capabilities."""
    with connect(ALICE, ALICE_PASSWORD, experimental=True) as client:
        yield client


@pytest.fixture
def tag() -> str:
    """Something unique to name what a test creates."""
    return uuid.uuid4().hex[:8]


class TestContacts:
    def test_create_then_show_and_delete_by_number(self, alice, server, tag):
        requires_method(server, "AddressBook/get", "ContactCard/query", "ContactCard/set")
        alice.ok("contact", "create", "--name", f"Ada {tag}", "--email", f"ada-{tag}@example.com")
        cards = alice.ok("contact", "list", "--json").json()
        [number] = [
            n for n, card in enumerate(cards, start=1) if card["name"]["full"] == f"Ada {tag}"
        ]
        card = alice.ok("contact", "show", str(number), "--json").json()
        assert card["id"] == cards[number - 1]["id"]
        alice.ok("contact", "delete", str(number), "--yes")
        assert card["id"] not in {
            item["id"] for item in alice.ok("contact", "list", "--json").json()
        }


class TestCalendars:
    def test_create_list_and_delete_an_event(self, alice, server, tag):
        requires_method(server, "Calendar/get", "CalendarEvent/query", "CalendarEvent/set")
        title = f"jmaplib cli {tag}"
        window = ("--from", "2030-01-07", "--to", "+1d", "--tz", "UTC", "--json")
        made = alice.ok(
            "event",
            "create",
            title,
            "--start",
            "2030-01-07T09:00",
            "--duration",
            "30m",
            "--tz",
            "UTC",
            "--json",
        ).json()
        event_id = made["created"]["event"]["id"]
        try:
            titles = {event.get("title") for event in alice.ok("event", "list", *window).json()}
            assert title in titles
        finally:
            alice.ok("event", "delete", event_id, "--yes")
        titles = {event.get("title") for event in alice.ok("event", "list", *window).json()}
        assert title not in titles


class TestFiles:
    def test_mkdir_upload_list_download_and_remove(self, alice, server, tag, tmp_path):
        requires_method(server, "FileNode/query", "FileNode/get", "FileNode/set")
        top = f"jmaplib-cli-{tag}"
        alice.ok("file", "mkdir", "-p", f"/{top}/sub")
        try:
            local = tmp_path / "note.txt"
            local.write_text("hello", encoding="utf-8")
            alice.ok("file", "upload", str(local), f"/{top}/sub/")
            assert top in {node["name"] for node in alice.ok("file", "ls", "--json").json()}
            listed = alice.ok("file", "tree", f"/{top}", "--json").json()
            assert [node["name"] for node in listed] == ["sub", "note.txt"]
            assert (
                alice.ok("file", "download", f"/{top}/sub/note.txt", "--save", "-").out == "hello"
            )
        finally:
            alice.ok("file", "rm", f"/{top}", "-r", "--yes")
        assert top not in {node["name"] for node in alice.ok("file", "ls", "--json").json()}


@requires_second_account
class TestSharing:
    def test_grant_and_revoke_a_mailbox(self, alice, server, tag):
        requires_method(server, "Mailbox/set", "Principal/query", "Principal/get")
        name = f"jmaplib-share-{tag}"
        alice.ok("mailbox", "create", name)
        try:
            alice.ok("share", "grant", f"mailbox:{name}", BOB, "--read")
            shared = alice.ok("share", "list", f"mailbox:{name}", "--json").json()
            [rights] = (shared.get("shareWith") or {}).values()
            assert rights["mayReadItems"] is True
            alice.ok("share", "revoke", f"mailbox:{name}", BOB)
            shared = alice.ok("share", "list", f"mailbox:{name}", "--json").json()
            assert not shared.get("shareWith")
        finally:
            alice.ok("mailbox", "delete", name, "--yes")
