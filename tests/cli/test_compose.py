"""``jmap compose``, ``send``, ``reply`` and ``forward``: one request per send."""

from __future__ import annotations

import io
import subprocess
from typing import Any

import pytest

from jmap.cli.app import run
from tests.fake.mailstore import MailStore, email

from .conftest import ScriptedPrompter


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


SEND = ("send", "--to", "bob@example.com", "--subject", "Hi", "--body", "Hello Bob")


@pytest.fixture(autouse=True)
def original(mailer: MailStore) -> dict[str, Any]:
    item = email(
        "e1", "Invoice", sender="bob@example.com", received="2026-10-28T09:00:00Z", attachment=True
    )
    item["to"] = [{"name": "Alice", "email": "alice@work.example"}, {"email": "carol@example.com"}]
    mailer.add(item)
    return item


def newest(store: MailStore) -> dict[str, Any]:
    return list(store.emails.values())[-1]


class TestSend:
    def test_one_request_into_sent(self, invoke, mailer, server):
        result = invoke(*SEND)
        assert result.code == 0, result.err
        assert "sent to bob@example.com" in result.err
        sent = newest(mailer)
        assert set(sent["mailboxIds"]) == {"sent"}
        assert sent["keywords"] == {"$seen": True}
        assert sent["from"] == [{"name": "Alice", "email": "alice@example.com"}]
        names = [call[0] for call in server.requests[-1]["methodCalls"]]
        assert names == ["Email/set", "EmailSubmission/set"]

    def test_json_is_both_set_responses(self, invoke):
        made, sent = invoke(*SEND, "--json").json()
        assert "draft" in made["created"]
        assert "send" in sent["created"]

    def test_from_another_identity_with_markdown_and_a_file(self, invoke, mailer, tmp_path):
        menu = tmp_path / "menu.pdf"
        menu.write_bytes(b"%PDF")
        result = invoke(
            "send",
            "--to",
            "Bob <bob@example.com>, carol@example.com",
            "--subject",
            "Md",
            "--body",
            "*hi*",
            "--markdown",
            "--from",
            "alice@work.example",
            "--attach",
            str(menu),
            "--keyword",
            "work",
        )
        assert result.code == 0, result.err
        created = newest(mailer)["sent"]
        assert created["from"][0]["email"] == "alice@work.example"
        assert created["bodyValues"]["html"]["value"] == "<p><em>hi</em></p>\n"
        assert created["attachments"][0]["name"] == "menu.pdf"
        assert created["attachments"][0]["type"] == "application/pdf"
        assert created["keywords"]["work"] is True

    def test_an_html_body(self, invoke, mailer):
        invoke("send", "--to", "b@x.org", "--body", "<p>a</p>", "--html")
        assert newest(mailer)["sent"]["bodyValues"]["text"]["value"] == "a"

    def test_the_body_from_a_file_with_headers(self, invoke, mailer, tmp_path):
        draft = tmp_path / "draft.txt"
        draft.write_text("To: dave@example.com\nSubject: From a file\n\nThe body.\n")
        assert invoke("send", "--body-file", str(draft)).code == 0
        sent = newest(mailer)
        assert sent["subject"] == "From a file"
        assert sent["to"] == [{"email": "dave@example.com"}]

    def test_the_body_from_stdin(self, invoke, mailer):
        invoke("send", "--to", "b@x.org", "--body-file", "-", stdin="piped text")
        assert newest(mailer)["bodyValues"]["1"]["value"] == "piped text"

    def test_held_until_later(self, invoke, mailer, server):
        server.capabilities["urn:ietf:params:jmap:submission"] = {
            "maxDelayedSend": 86400,
            "submissionExtensions": {"FUTURERELEASE": []},
        }
        result = invoke(*SEND, "--send-at", "+2h")
        assert result.code == 0, result.err
        assert "it goes out at 2026-10-30T12:00:00Z" in result.err
        submission = next(iter(mailer.submissions.values()))
        assert submission["envelope"]["mailFrom"]["parameters"] == {"HOLDFOR": "7200"}
        assert submission["envelope"]["rcptTo"] == [{"email": "bob@example.com"}]

    def test_a_hold_the_server_cannot_do(self, invoke, server):
        assert invoke(*SEND, "--send-at", "+2h").code == 7
        server.capabilities["urn:ietf:params:jmap:submission"] = {
            "maxDelayedSend": 60,
            "submissionExtensions": {"FUTURERELEASE": []},
        }
        assert invoke(*SEND, "--send-at", "+2h").code == 2

    def test_a_server_that_files_sent_mail_itself(self, invoke, mailer, environ):
        invoke("config", "set", "accounts.work.send_move_to_sent", "false")
        invoke(*SEND)
        assert set(newest(mailer)["mailboxIds"]) == {"drafts"}

    def test_a_refused_send_stays_in_drafts(self, invoke, mailer):
        mailer.refuse_submission = "forbiddenFrom"
        result = invoke(*SEND)
        assert result.code == 8
        assert "not sent (forbiddenFrom); it is saved in Drafts" in result.err
        assert "jmap draft send id:" in result.err
        assert set(newest(mailer)["mailboxIds"]) == {"drafts"}

    def test_a_failed_submission_call(self, invoke, server):
        server.fail("EmailSubmission/set", "forbidden")
        assert invoke(*SEND).code == 8

    def test_a_refused_message(self, invoke, server):
        server.respond(
            "Email/set", {"accountId": "a", "notCreated": {"draft": {"type": "tooLarge"}}}
        )
        result = invoke(*SEND)
        assert result.code == 8
        assert "not created: tooLarge" in result.err

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("send", "--subject", "x", "--body", "y"), "no recipients"),
            (("send", "--to", "b@x.org"), "is empty"),
            (("send", "--to", "bob"), "is not an email address"),
            (("send", "--to", "b@x.org", "--body", "x", "--attach", "/no/such"), "cannot read"),
            (("send", "--to", "b@x.org", "--body", "x", "--send-at", "soon"), "is not +2h"),
            (("send", "--to", "b@x.org", "--body", "x", "--keyword", "a b"), "keyword"),
        ],
    )
    def test_usage_errors(self, invoke, args, message):
        result = invoke(*args)
        assert result.code == 2
        assert message in result.err

    def test_an_unknown_identity(self, invoke):
        result = invoke(*SEND, "--from", "nobody@example.com")
        assert result.code == 9
        assert "no identity sends as" in result.err

    def test_no_drafts_mailbox(self, invoke, mailer):
        del mailer.mailboxes["drafts"]
        assert invoke(*SEND).code == 9

    def test_a_whole_message_file(self, invoke, mailer, tmp_path):
        eml = tmp_path / "m.eml"
        eml.write_bytes(b"Subject: Raw\r\nTo: bob@example.com\r\n\r\nbody")
        result = invoke("send", "--eml", str(eml))
        assert result.code == 0, result.err
        assert "sent to its recipients" in result.err
        assert newest(mailer)["subject"] == "Raw"
        assert invoke("send", "--eml", "-", stdin="Subject: Piped\r\n\r\nx").code == 0


class TestCompose:
    def test_in_the_editor(self, invoke, editor, mailer):
        fake = editor(
            lambda text: (
                text.replace("To: ", "To: bob@example.com").replace("Subject: ", "Subject: Edited")
                + "Written in the editor.\n"
            )
        )
        result = invoke("compose", "--edit")
        assert result.code == 0, result.err
        assert "# You can send as: Alice <alice@example.com>" in fake.seen[0]
        assert newest(mailer)["subject"] == "Edited"

    def test_unchanged_is_not_sent(self, invoke, editor):
        editor(lambda text: text)
        result = invoke("compose", "--edit")
        assert result.code == 1
        assert "was not changed" in result.err

    def test_a_mistake_is_edited_again(self, invoke, editor, mailer):
        fake = editor(
            lambda text: "not a header\n\nbody",
            lambda text: "To: bob@example.com\nSubject: Fixed\n\nbody",
        )
        result = invoke("compose", "--edit", prompter=ScriptedPrompter(confirms=[True]))
        assert result.code == 0, result.err
        assert fake.seen[1].startswith("# Error: line 1:")

    def test_a_mistake_without_another_go(self, invoke, editor):
        editor(lambda text: "not a header\n\nbody")
        assert invoke("compose", "--edit").code == 2

    def test_a_failing_editor(self, invoke, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 1)
        )
        assert invoke("compose", "--edit").code == 1

    def test_save_as_a_draft(self, invoke, mailer):
        result = invoke("compose", "--to", "x@y.z", "--body", "later", "--draft")
        assert "saved 1 draft(s)" in result.err
        assert set(newest(mailer)["mailboxIds"]) == {"drafts"}
        assert "$draft" in newest(mailer)["keywords"]

    def test_the_editor_opens_by_default_only_on_a_terminal(self, make_context, editor):
        fake = editor(lambda text: text.replace("To: ", "To: b@x.org") + "x\n")
        piped = make_context()
        assert run(["compose", "--draft"], context=piped) == 2  # nothing to save
        assert fake.seen == []
        typed = make_context()
        typed.stdin = TTY()
        assert run(["compose", "--draft"], context=typed) == 0
        assert len(fake.seen) == 1


class TestReplyAndForward:
    def test_reply_all_quotes_threads_and_marks_answered(self, invoke, mailer, server):
        result = invoke("reply", "e1", "--all", "--body", "Thanks!")
        assert result.code == 0, result.err
        reply = newest(mailer)
        assert reply["subject"] == "Re: Invoice"
        assert reply["from"][0]["email"] == "alice@work.example"
        assert [a["email"] for a in reply["to"]] == ["bob@example.com"]
        assert [a["email"] for a in reply["cc"]] == ["carol@example.com"]
        assert reply["inReplyTo"] == ["e1@example.com"]
        assert reply["bodyValues"]["1"]["value"].startswith("Thanks!\n\nOn ")
        assert mailer.emails["e1"]["keywords"]["$answered"] is True
        assert server.requests[-1]["methodCalls"][0][1]["update"] == {
            "e1": {"keywords/$answered": True}
        }

    def test_a_failed_reply_takes_answered_back(self, invoke, mailer):
        mailer.refuse_submission = "forbiddenToSend"
        assert invoke("reply", "e1", "--body", "x", "--no-quote").code == 8
        assert "$answered" not in mailer.emails["e1"]["keywords"]

    def test_forward_with_attachments(self, invoke, mailer):
        result = invoke("forward", "e1", "--to", "dave@example.com", "--body", "FYI")
        assert result.code == 0, result.err
        forwarded = newest(mailer)
        assert forwarded["subject"] == "Fwd: Invoice"
        assert forwarded["attachments"][0]["blobId"] == "b-e1-3"
        assert mailer.emails["e1"]["keywords"]["$forwarded"] is True

    def test_forward_the_whole_message(self, invoke, mailer):
        invoke("forward", "e1", "--to", "d@x.org", "--attach-original", "--body", "see attached")
        assert newest(mailer)["attachments"][0]["type"] == "message/rfc822"

    def test_a_missing_original(self, invoke, server):
        assert invoke("reply", "gone", "--body", "x").code == 9
        assert invoke("forward", "gone", "--to", "d@x.org").code == 9


def test_no_identity_to_send_from(invoke, mailer):
    mailer.identities.clear()
    result = invoke(*SEND)
    assert result.code == 9
    assert "no identity sends as the From address" in result.err


def test_an_identity_without_a_name(invoke, mailer):
    mailer.identities["i1"]["name"] = ""
    invoke(*SEND)
    assert newest(mailer)["from"] == [{"email": "alice@example.com"}]


def test_sending_in_the_shell_reuses_the_mailboxes(make_context, mailer, server):
    from jmap.cli.shell import Shell

    context = make_context()
    shell = Shell(context, context.flags)
    shell.execute("ls")
    before = len(server.requests)
    shell.execute("send --to b@x.org --subject s --body b")
    sent_calls = [
        call[0] for request in server.requests[before:] for call in request["methodCalls"]
    ]
    assert "Mailbox/get" not in sent_calls
    context.close()
