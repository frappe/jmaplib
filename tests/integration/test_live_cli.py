"""The ``jmap`` command against a real server: accounts, mail and Sieve.

``tests/cli`` drives the command tree against the fake server. These tests drive
the same tree, in process, against the server ``JMAP_TEST_URL`` names, as the
people in :mod:`tests.integration.cli_people`. Contacts, calendars, files and
sharing are in ``test_live_cli_groupware.py``.

Skips are per method, as in the other live tests: a server that lacks a method
skips the test that needs it. The library client is what asks, because the
command's own refusal (exit 7) would read as a failure.
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from jmap.__about__ import __version__
from jmap.mail.ops import queue_thread
from tests.integration.cli_people import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    Person,
    requires_method,
    requires_second_account,
    requires_server,
)
from tests.integration.conftest import connect

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jmap.client import JMAPClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def alice(tmp_path_factory: pytest.TempPathFactory) -> Person:
    person = Person(tmp_path_factory.mktemp("alice"), ALICE, ALICE_PASSWORD)
    person.add_account()
    return person


@pytest.fixture(scope="module")
def bob(tmp_path_factory: pytest.TempPathFactory) -> Person:
    person = Person(tmp_path_factory.mktemp("bob"), BOB, BOB_PASSWORD)
    person.add_account()
    return person


@pytest.fixture(scope="module")
def delivered(alice: Person, bob: Person, server: JMAPClient) -> dict[str, Any]:
    """A message alice sent bob, as bob's ``mail list`` shows it."""
    requires_method(server, "Identity/get", "Email/set", "EmailSubmission/set")
    subject = f"jmaplib cli test {uuid.uuid4().hex[:8]}"
    alice.ok("send", "--to", BOB, "--subject", subject, "--body", "sent by jmap send")
    found = _wait_for(bob, subject)
    assert found, f"{subject!r} never reached bob's inbox"
    return found


@pytest.fixture(scope="module")
def server() -> Iterator[JMAPClient]:
    """A library client for alice, to ask what the server implements."""
    with connect(ALICE, ALICE_PASSWORD) as client:
        yield client


class TestTheModule:
    def test_python_m_jmap_runs_the_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "jmap", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"jmap {__version__}"


@requires_server
class TestAccount:
    def test_list_shows_the_added_account(self, alice):
        listed = alice.ok("account", "list", "--json").json()
        assert [item["name"] for item in listed] == ["live"]

    def test_it_connects(self, alice):
        alice.ok("account", "test")


@requires_server
class TestServer:
    def test_capabilities_name_the_core(self, alice):
        rows = alice.ok("server", "capabilities", "--json").json()
        assert "urn:ietf:params:jmap:core" in {row["urn"] for row in rows}


@requires_server
class TestMail:
    def test_mailbox_list_has_an_inbox(self, alice, server):
        requires_method(server, "Mailbox/get")
        mailboxes = alice.ok("mailbox", "list", "--json").json()
        assert "inbox" in {mailbox.get("role") for mailbox in mailboxes}

    def test_mail_list_prints_an_array_of_messages(self, alice, server):
        requires_method(server, "Mailbox/get", "Email/query", "Email/get")
        messages = alice.ok("mail", "list", "--json").json()
        assert isinstance(messages, list)
        assert all(message["id"] for message in messages)


@requires_server
@requires_second_account
class TestDelivery:
    """Alice sends with ``jmap send``; bob finds it with ``jmap mail list``."""

    def test_bob_lists_what_alice_sent(self, delivered):
        assert delivered["subject"].startswith("jmaplib cli test")

    def test_the_thread_holds_the_message(self, bob, server, delivered):
        requires_method(server, "Thread/get")
        rows = bob.ok("mail", "thread", delivered["id"], "--json").json()
        assert delivered["id"] in {row["id"] for row in rows}

    def test_the_sent_copy_is_read_and_filed_in_sent(self, alice, delivered):
        """``onSuccessUpdateEmail`` moved it out of Drafts and cleared ``$draft``."""
        subject = delivered["subject"]
        sent = alice.ok("mail", "list", "--in", "sent", "--subject", subject, "--json").json()
        assert [item["keywords"] for item in sent] == [{"$seen": True}]
        drafts = alice.ok("mail", "list", "--in", "drafts", "--subject", subject, "--json")
        assert drafts.json() == []

    def test_a_thread_takes_one_request(self, alice, server, delivered):
        """The flattened ``/list/*/emailIds`` reference works, so no fallback is needed."""
        requires_method(server, "Thread/get")
        [sent] = alice.ok("mail", "list", "-A", "--subject", delivered["subject"], "--json").json()
        with server.batch() as batch:
            handles = queue_thread(batch, sent["id"])
        assert handles.emails.error is None
        assert sent["id"] in {email.id for email in handles.emails.result.items}


@requires_server
class TestSieve:
    def test_put_stores_a_script_that_list_shows(self, alice, server, tmp_path):
        requires_method(server, "SieveScript/get", "SieveScript/set", "SieveScript/validate")
        name = f"jmaplib-cli-{uuid.uuid4().hex[:8]}"
        script = tmp_path / "filter.sieve"
        script.write_text("keep;\r\n", encoding="utf-8")
        alice.ok("sieve", "put", str(script), "--name", name)
        try:
            listed = alice.ok("sieve", "list", "--json").json()
            assert name in {item["name"] for item in listed}
        finally:
            alice.ok("sieve", "delete", name, "--yes")


def _wait_for(person: Person, subject: str, attempts: int = 20) -> dict[str, Any] | None:
    """Poll the inbox, because delivery is asynchronous."""
    for _ in range(attempts):
        messages = person.ok("mail", "list", "--subject", subject, "--json").json()
        if messages:
            found: dict[str, Any] = messages[0]
            return found
        time.sleep(1)
    return None
