"""The ``jmap`` command against a real server.

``tests/cli`` drives the command tree against the fake server. These tests drive
the same tree, in process, against the server ``JMAP_TEST_URL`` names: each
person gets an empty configuration directory, adds the account with
``--password-stdin --no-keyring``, and runs commands with ``--json``.

Skips are per method, as in the other live tests: a server that lacks a method
skips the test that needs it. The library client is what asks, because the
command's own refusal (exit 7) would read as a failure.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from jmap.__about__ import __version__
from jmap.cli.app import run
from jmap.cli.context import CLIContext
from jmap.cli.secrets import MemoryStore
from tests.integration.conftest import CA_BUNDLE, INSECURE, JMAP_URL, connect

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from jmap.client import JMAPClient

pytestmark = pytest.mark.integration

ALICE = os.environ.get("JMAP_TEST_USER", "")
ALICE_PASSWORD = os.environ.get("JMAP_TEST_PASS", "")
BOB = os.environ.get("JMAP_TEST_USER2", "")
BOB_PASSWORD = os.environ.get("JMAP_TEST_PASS2", "")

requires_server = pytest.mark.skipif(
    not (JMAP_URL and ALICE and ALICE_PASSWORD),
    reason="set JMAP_TEST_URL, JMAP_TEST_USER and JMAP_TEST_PASS to run",
)
requires_second_account = pytest.mark.skipif(
    not (BOB and BOB_PASSWORD),
    reason="set JMAP_TEST_USER2 and JMAP_TEST_PASS2 to run delivery tests",
)


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)


class Person:
    """One person's ``jmap``: an empty home directory, and no keyring.

    ``account add`` reads the password from stdin. With ``--no-keyring`` it is
    not stored, so later commands get it from ``$JMAP_PASSWORD``, as in CI.
    """

    def __init__(self, home: Path, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.environ = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
        }

    def __call__(self, *args: str) -> Result:
        environ = {**self.environ, "JMAP_PASSWORD": self.password}
        return self._run([*args, "--no-keyring"], environ, stdin="")

    def ok(self, *args: str) -> Result:
        """Run a command that must succeed."""
        return _succeeded(args, self(*args))

    def add_account(self) -> Result:
        args = ["account", "add", "live", "--url", JMAP_URL, "--username", self.username]
        args += ["--password-stdin", "--no-keyring"]
        if CA_BUNDLE:
            args += ["--ca-bundle", CA_BUNDLE]
        if INSECURE:
            args.append("--insecure")
        return _succeeded(args, self._run(args, self.environ, stdin=f"{self.password}\n"))

    def _run(self, args: list[str], environ: dict[str, str], stdin: str) -> Result:
        context = CLIContext(
            environ=environ,
            stdin=io.StringIO(stdin),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            secrets=MemoryStore(),
            width=200,
        )
        code = run(args, context=context)
        return Result(code, _text(context.out.file), _text(context.err.file))


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
def server() -> Iterator[JMAPClient]:
    """A library client for alice, to ask what the server implements."""
    with connect(ALICE, ALICE_PASSWORD) as client:
        yield client


def requires_method(client: JMAPClient, *methods: str) -> None:
    for method in methods:
        if not client.capabilities.supports(method):
            pytest.skip(f"server does not implement {method}")


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

    @pytest.fixture(scope="class")
    def delivered(self, alice: Person, bob: Person, server: JMAPClient) -> dict[str, Any]:
        requires_method(server, "Identity/get", "Email/set", "EmailSubmission/set")
        subject = f"jmaplib cli test {uuid.uuid4().hex[:8]}"
        alice.ok("send", "--to", BOB, "--subject", subject, "--body", "sent by jmap send")
        found = _wait_for(bob, subject)
        assert found, f"{subject!r} never reached bob's inbox"
        return found

    def test_bob_lists_what_alice_sent(self, delivered):
        assert delivered["subject"].startswith("jmaplib cli test")

    def test_the_thread_holds_the_message(self, bob, server, delivered):
        requires_method(server, "Thread/get")
        rows = bob.ok("mail", "thread", delivered["id"], "--json").json()
        assert delivered["id"] in {row["id"] for row in rows}


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


def _succeeded(args: Sequence[str], result: Result) -> Result:
    assert result.code == 0, f"jmap {' '.join(args)} exited {result.code}: {result.err}"
    return result


def _text(stream: Any) -> str:
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()
