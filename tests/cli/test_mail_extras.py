"""Drafts, attachments, identities, submissions, the vacation response, import and export."""

from __future__ import annotations

import io
import mailbox
from typing import TYPE_CHECKING

import pytest

from jmap.cli.app import run
from tests.fake.mailstore import MailStore, email

from .conftest import ScriptedPrompter, text_of

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@pytest.fixture(autouse=True)
def mail(mailer: MailStore, server: FakeJMAPServer) -> MailStore:
    first = email("e1", "Invoice", attachment=True, received="2026-10-28T09:00:00Z")
    first["attachments"].append(
        {"partId": "4", "type": "text/csv", "name": "rows.csv", "size": 10, "blobId": "b-e1-4"}
    )
    mailer.add(
        first,
        email("e2", "Plain", received="2026-10-29T09:00:00Z"),
        email(
            "d1",
            "Unfinished",
            mailboxes=("drafts",),
            keywords=("$draft",),
            received="2026-10-30T09:00:00Z",
        ),
    )
    mailer.emails["d1"]["to"] = [{"email": "bob@example.com"}]
    mailer.emails["d1"]["from"] = [{"name": "Alice", "email": "alice@example.com"}]
    for blob in ("b-e1-3", "b-e1-4", "blob-e1", "blob-e2"):
        server.blobs[blob] = (f"data of {blob}".encode(), "application/octet-stream")
    return mailer


class TestDraft:
    def test_list(self, invoke):
        rows = [line.split("\t") for line in invoke("draft", "list").out.splitlines()]
        assert [row[4] for row in rows] == ["Unfinished"]

    def test_new(self, invoke, mail):
        result = invoke("draft", "new", "--to", "c@x.org", "--subject", "Later", "--body", "b")
        assert "saved 1 draft(s)" in result.err
        assert list(mail.emails.values())[-1]["subject"] == "Later"

    def test_edit_replaces_it(self, invoke, mail, editor):
        fake = editor(lambda text: text.replace("Subject: Unfinished", "Subject: Finished"))
        result = invoke("draft", "edit", "d1")
        assert result.code == 0, result.err
        assert "To: bob@example.com" in fake.seen[0]
        assert "d1" not in mail.emails
        assert list(mail.emails.values())[-1]["subject"] == "Finished"

    def test_edit_and_send(self, invoke, mail, editor):
        editor(lambda text: text + "more\n")
        assert invoke("draft", "edit", "d1", "--send").code == 0
        assert set(list(mail.emails.values())[-1]["mailboxIds"]) == {"sent"}

    def test_send(self, invoke, mail):
        result = invoke("draft", "send", "d1")
        assert result.code == 0, result.err
        assert set(mail.emails["d1"]["mailboxIds"]) == {"sent"}
        assert "$draft" not in mail.emails["d1"]["keywords"]
        assert invoke("draft", "send", "d1", "--json").json()["created"]

    def test_send_without_moving_to_sent(self, invoke, mail):
        invoke("config", "set", "accounts.work.send_move_to_sent", "false")
        invoke("draft", "send", "d1")
        assert set(mail.emails["d1"]["mailboxIds"]) == {"drafts"}

    def test_a_refused_send(self, invoke, mail):
        mail.refuse_submission = "forbiddenFrom"
        result = invoke("draft", "send", "d1")
        assert result.code == 8
        assert "jmap draft send id:d1" in result.err

    def test_missing(self, invoke, editor):
        assert invoke("draft", "send", "gone").code == 9
        assert invoke("draft", "edit", "gone").code == 9

    def test_delete(self, invoke, mail):
        assert invoke("draft", "delete", "d1").code == 1  # no terminal, no --yes
        result = invoke("draft", "delete", "d1", prompter=ScriptedPrompter(confirms=[True]))
        assert "deleted 1 draft(s)" in result.err
        assert "d1" not in mail.emails


class TestAttachment:
    def test_list(self, invoke):
        rows = [line.split("\t") for line in invoke("attachment", "list", "e1").out.splitlines()]
        assert rows == [
            ["1", "invoice.pdf", "application/pdf", "2.0K"],
            ["2", "rows.csv", "text/csv", "10B"],
        ]

    def test_download_by_number_and_name_without_overwriting(self, invoke, tmp_path):
        result = invoke("attachment", "download", "e1", "1", "rows.csv", "--dir", str(tmp_path))
        assert result.code == 0, result.err
        assert (tmp_path / "invoice.pdf").read_bytes() == b"data of b-e1-3"
        invoke("attachment", "download", "e1", "--all", "--dir", str(tmp_path))
        assert (tmp_path / "invoice (1).pdf").exists()
        assert (tmp_path / "rows (1).csv").exists()
        invoke("attachment", "download", "e1", "1", "--dir", str(tmp_path))
        assert (tmp_path / "invoice (2).pdf").exists()
        invoke("attachment", "download", "e1", "1", "--dir", str(tmp_path), "--force")
        assert not (tmp_path / "invoice (3).pdf").exists()

    def test_save_one_to_a_file_or_stdout(self, invoke, tmp_path):
        target = tmp_path / "x.bin"
        assert invoke("attachment", "download", "e1", "2", "--save", str(target)).code == 0
        assert target.read_bytes() == b"data of b-e1-4"
        assert invoke("attachment", "download", "e1", "2", "--save", "-").out == "data of b-e1-4"
        assert invoke("attachment", "download", "e1", "2", "--save", str(target)).code == 2
        assert (
            invoke("attachment", "download", "e1", "2", "--save", str(target), "--force").code == 0
        )
        assert invoke("attachment", "download", "e1", "--all", "--save", str(target)).code == 2

    def test_choosing(self, invoke, mail, tmp_path):
        result = invoke("attachment", "download", "e1", "--dir", str(tmp_path))
        assert result.code == 2
        assert "which attachment? 1. invoice.pdf, 2. rows.csv" in result.err
        assert invoke("attachment", "download", "e1", "9").code == 9
        mail.emails["e1"]["attachments"].pop()
        assert invoke("attachment", "download", "e1", "--dir", str(tmp_path)).code == 0
        assert invoke("attachment", "download", "e2").code == 9
        assert invoke("attachment", "list", "gone").code == 9


class TestIdentity:
    def test_list_and_show(self, invoke):
        rows = [line.split("\t") for line in invoke("identity", "list").out.splitlines()]
        assert [row[2] for row in rows] == ["alice@example.com", "alice@work.example"]
        assert "signature\t" in invoke("identity", "show", "ALICE@example.com").out
        assert invoke("identity", "show", "nobody").code == 9

    def test_create_update_delete(self, invoke, mail):
        result = invoke(
            "identity",
            "create",
            "--email",
            "a@new.example",
            "--name",
            "New",
            "--reply-to",
            "r@new.example",
            "--bcc",
            "",
            "--signature",
            "-- A",
        )
        assert result.code == 0, result.err
        assert "created the identity New <a@new.example>" in result.err
        made = list(mail.identities.values())[-1]
        assert made["replyTo"] == [{"email": "r@new.example"}]
        assert made["bcc"] is None
        invoke("identity", "update", "a@new.example", "--html-signature", "<b>A</b>")
        assert made["htmlSignature"] == "<b>A</b>"
        assert "R@new.example" not in invoke("identity", "list").out
        assert invoke("identity", "update", "a@new.example").code == 2
        assert invoke("identity", "update", "i1", "--bcc", "nope").code == 2
        assert invoke("identity", "delete", "i1", "-y").code == 8
        deleted = invoke("identity", "delete", str(made["id"]), "-y")
        assert f"deleted the identity New <a@new.example> ({made['id']})" in deleted.err
        unnamed = invoke("identity", "create", "--email", "b@new.example")
        assert "created the identity b@new.example\n" in unnamed.err


class TestSubmission:
    def test_list_show_and_cancel(self, invoke, mail, server):
        server.capabilities["urn:ietf:params:jmap:submission"] = {
            "maxDelayedSend": 86400,
            "submissionExtensions": {"FUTURERELEASE": []},
        }
        invoke("send", "--to", "b@x.org", "--subject", "Later", "--body", "x", "--send-at", "+1h")
        invoke("send", "--to", "b@x.org", "--subject", "Now", "--body", "x")
        rows = [line.split("\t") for line in invoke("submission", "list").out.splitlines()]
        assert [(row[1], row[3]) for row in rows] == [("pending", "Later"), ("final", "Now")]
        pending = [
            line.split("\t") for line in invoke("submission", "list", "--pending").out.splitlines()
        ]
        held = pending[0][0]
        assert len(pending) == 1
        assert f"id\t{held}" in invoke("submission", "show", held).out
        assert invoke("submission", "cancel", held).code == 0
        assert mail.submissions[held]["undoStatus"] == "canceled"
        assert invoke("submission", "show", "nope").code == 9

    def test_delivery_status(self, invoke, mail):
        mail.submissions["s1"] = {
            "id": "s1",
            "undoStatus": "final",
            "emailId": "e1",
            "deliveryStatus": {"b@x.org": {"delivered": "yes"}},
        }
        assert "delivery\tb@x.org: yes" in invoke("submission", "show", "s1").out
        mail.submissions["s1"]["deliveryStatus"] = None
        assert "delivery\t\n" in invoke("submission", "show", "s1").out


class TestVacation:
    def test_show_set_enable_disable(self, invoke, mail, tmp_path):
        assert "enabled\tno" in invoke("vacation", "show").out
        body = tmp_path / "away.txt"
        body.write_text("Back on Monday.")
        result = invoke(
            "vacation",
            "set",
            "--subject",
            "Away",
            "--body-file",
            str(body),
            "--from",
            "2026-12-20T00:00:00Z",
            "--until",
            "",
            "--enable",
        )
        assert result.code == 0, result.err
        assert mail.vacation["textBody"] == "Back on Monday."
        assert mail.vacation["toDate"] is None
        assert enabled(mail)
        invoke("vacation", "disable")
        assert not enabled(mail)
        invoke("vacation", "enable")
        assert enabled(mail)

    def test_errors(self, invoke):
        assert invoke("vacation", "set").code == 2
        assert invoke("vacation", "set", "--body-file", "/no/such").code == 2

    def test_no_object(self, invoke, server):
        server.respond("VacationResponse/get", {"accountId": "a", "list": [], "notFound": []})
        assert invoke("vacation", "show", "--json").json() == {}


def enabled(mail: MailStore) -> bool:
    return bool(mail.vacation["isEnabled"])


class TestImportExport:
    def test_import_a_file_and_stdin(self, invoke, mail, tmp_path):
        eml = tmp_path / "m.eml"
        eml.write_bytes(b"Subject: Imported\r\n\r\nbody")
        result = invoke(
            "mail",
            "import",
            str(eml),
            "--to",
            "archive",
            "--seen",
            "--keyword",
            "flagged",
            "--received-at",
            "2026-01-01T00:00:00Z",
        )
        assert result.code == 0, result.err
        imported = list(mail.emails.values())[-1]
        assert set(imported["mailboxIds"]) == {"arch"}
        assert set(imported["keywords"]) == {"$flagged", "$seen"}
        again = invoke("mail", "import", str(eml))
        assert again.code == 0
        assert "already there" in again.err
        assert invoke("mail", "import", str(eml), "--strict").code == 8
        assert invoke("mail", "import", "-", stdin="Subject: Piped\r\n\r\nx").code == 0
        assert invoke("mail", "import", "/no/such.eml").code == 2

    def test_import_from_a_binary_stdin(self, make_context, mail):
        context = make_context()
        context.stdin = io.TextIOWrapper(io.BytesIO(b"Subject: Bytes\r\n\r\nx"))
        assert run(["mail", "import", "-"], context=context) == 0
        assert text_of(context.err.file).startswith("imported 1")

    def test_export_files_stdout_and_mbox(self, invoke, tmp_path):
        result = invoke("mail", "export", "e1", "e2", "--dir", str(tmp_path))
        assert result.code == 0, result.err
        assert (tmp_path / "Invoice.eml").read_bytes() == b"data of blob-e1"
        invoke("mail", "export", "e1", "--dir", str(tmp_path))
        assert (tmp_path / "Invoice (1).eml").exists()
        assert invoke("mail", "export", "e2", "--save", "-").out == "data of blob-e2"
        target = tmp_path / "one.eml"
        assert invoke("mail", "export", "e2", "--save", str(target)).code == 0
        assert invoke("mail", "export", "e1", "e2", "--save", str(target)).code == 2
        box = tmp_path / "all.mbox"
        assert invoke("mail", "export", "e1", "e2", "--mbox", str(box)).code == 0
        assert len(mailbox.mbox(str(box))) == 2

    def test_export_reports_what_is_missing(self, invoke, tmp_path):
        result = invoke("mail", "export", "e1", "gone", "--dir", str(tmp_path))
        assert result.code == 8
        assert "not found: gone" in result.err


def test_send_eml_from_a_binary_stdin(make_context, mail):
    context = make_context()
    context.stdin = io.TextIOWrapper(io.BytesIO(b"Subject: Raw\r\n\r\nx"))
    assert run(["send", "--eml", "-"], context=context) == 0
