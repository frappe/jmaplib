"""``--read-only`` and ``--dry-run``: what may reach the server.

The fake server records every request and upload, so each test can check that
a refused or dry-run change never arrived.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.config import AccountConfig, Config, save_config
from jmap.cli.errors import ExitCode
from jmap.cli.paths import config_path
from tests.cli.conftest import URL
from tests.fake.mailstore import MailStore, email

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

pytestmark = pytest.mark.usefixtures("mailer")


@pytest.fixture(autouse=True)
def mail(mailer: MailStore) -> MailStore:
    mailer.add(email("e1", "Invoice", received="2026-10-28T09:00:00Z"))
    return mailer


def sent_methods(server: FakeJMAPServer) -> list[str]:
    return [call[0] for request in server.requests for call in request["methodCalls"]]


class TestReadOnly:
    def test_reading_works_and_marks_nothing_read(self, invoke, mail, server):
        result = invoke("--read-only", "read", "e1")
        assert result.code == 0, result.err
        assert "Invoice" in result.out
        assert "$seen" not in mail.emails["e1"]["keywords"]
        assert "Email/set" not in sent_methods(server)

    def test_a_change_is_refused_before_it_is_sent(self, invoke, mail, server):
        result = invoke("--read-only", "flag", "e1")
        assert result.code == ExitCode.READ_ONLY
        assert int(ExitCode.READ_ONLY) == 11
        assert "read-only: not sending Email/set" in result.err
        assert "Email/set" not in sent_methods(server)
        assert "$flagged" not in mail.emails["e1"]["keywords"]

    def test_a_send_is_refused_at_its_first_upload(self, invoke, server, tmp_path):
        attachment = tmp_path / "a.txt"
        attachment.write_text("x", encoding="utf-8")
        result = invoke(
            "--read-only", "send", "--to", "b@x.org", "--body", "x", "--attach", str(attachment)
        )
        assert result.code == 11
        assert "not uploading 1 bytes" in result.err
        assert server.blobs == {}

    def test_from_the_environment(self, invoke, server):
        assert invoke("flag", "e1", env={"JMAP_READ_ONLY": "1"}).code == 11

    def test_from_the_configuration_where_the_command_line_cannot_undo_it(
        self, invoke, environ, server
    ):
        stored = AccountConfig(url=URL, username="alice@example.com", read_only=True)
        config = Config(default_account="work", accounts={"work": stored})
        save_config(config, config_path(environ))
        assert invoke("flag", "e1").code == 11
        assert invoke("flag", "e1", env={"JMAP_READ_ONLY": "0"}).code == 11

    def test_in_the_shell_it_follows_each_line(self, make_context, mail):
        from jmap.cli.shell import Shell

        context = make_context()
        shell = Shell(context, context.flags)
        shell.execute("--read-only flag e1")
        assert "$flagged" not in mail.emails["e1"]["keywords"]
        shell.execute("flag e1")
        assert mail.emails["e1"]["keywords"]["$flagged"] is True
        context.close()


class TestDryRun:
    def test_the_change_is_printed_not_sent(self, invoke, mail, server):
        result = invoke("--dry-run", "delete", "e1", "--permanent")
        assert result.code == 0, result.err
        request = json.loads(result.out)
        assert request["methodCalls"][0][0] == "Email/set"
        assert request["methodCalls"][0][1]["destroy"] == ["e1"]
        assert "dry run: this is the change it would send; nothing was changed" in result.err
        assert "e1" in mail.emails
        assert "Email/set" not in sent_methods(server)

    def test_a_send_with_attachments_shows_the_whole_message(self, invoke, server, tmp_path):
        attachment = tmp_path / "menu.pdf"
        attachment.write_bytes(b"%PDF")
        result = invoke(
            "--dry-run",
            "send",
            "--to",
            "bob@example.com",
            "--subject",
            "Hi",
            "--body",
            "x",
            "--attach",
            str(attachment),
        )
        assert result.code == 0, result.err
        request = json.loads(result.out)
        names = [call[0] for call in request["methodCalls"]]
        assert names == ["Email/set", "EmailSubmission/set"]
        created = request["methodCalls"][0][1]["create"]["draft"]
        assert created["attachments"][0]["blobId"] == "dry-run-blob-1"
        assert "it would first upload 1 file(s), 4 bytes" in result.err
        assert server.blobs == {}
        assert "EmailSubmission/set" not in sent_methods(server)

    def test_reads_go_through(self, invoke, server):
        result = invoke("--dry-run", "ls")
        assert result.code == 0
        assert "Invoice" in result.out

    def test_several_requests_print_as_a_list(self, invoke, server, monkeypatch):
        from jmap.cli.guard import DryRunDone

        requests: list[dict[str, Any]] = [{"methodCalls": []}, {"methodCalls": []}]

        def stop(*_args: Any, **_kwargs: Any) -> None:
            raise DryRunDone(requests, [])

        monkeypatch.setattr("jmap.cli.commands.mail_triage._update", stop)
        assert json.loads(invoke("flag", "e1").out) == requests
