"""Drafts: addresses, the text form, the create object, holds and the sent patch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jmap.mail.addresses import InvalidAddressError, format_address, parse_addresses
from jmap.mail.compose import (
    AttachmentSpec,
    Draft,
    DraftParseError,
    Uploaded,
    draft_from_email,
    email_create,
    envelope,
    hold,
    parse_draft,
    render_draft,
    sent_patch,
)
from jmap.models.mail.create import InvalidEmailCreateError
from jmap.models.mail.objects import Email, EmailAddress

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


class TestAddresses:
    def test_names_quotes_and_bare_addresses(self):
        found = parse_addresses('"Doe, Jane" <jane@x.org>, bob@y.org,, ')
        assert [(a.name, a.email) for a in found] == [
            ("Doe, Jane", "jane@x.org"),
            (None, "bob@y.org"),
        ]

    def test_not_an_address(self):
        with pytest.raises(InvalidAddressError, match="'bob' is not an email address"):
            parse_addresses("bob")
        with pytest.raises(InvalidAddressError, match="Bob <bob>' is not"):
            parse_addresses("Bob <bob>")
        assert parse_addresses("") == []
        with pytest.raises(InvalidAddressError, match="'<<<' is not"):
            parse_addresses("<<<")

    def test_formatting_quotes_where_needed(self):
        assert (
            format_address(EmailAddress(name="Doe, Jane", email="j@x.org"))
            == '"Doe, Jane" <j@x.org>'
        )
        assert format_address(EmailAddress(email="b@y.org")) == "b@y.org"


class TestTextForm:
    def test_round_trip(self):
        draft = Draft(
            from_=[EmailAddress(name="Alice", email="a@x.org")],
            to=[EmailAddress(email="b@y.org")],
            reply_to=[EmailAddress(email="r@x.org")],
            subject="Lunch?",
            in_reply_to=["m1@x"],
            references=["m0@x", "m1@x"],
            attachments=[
                AttachmentSpec(path="menu.pdf", type="application/pdf"),
                AttachmentSpec(blob_id="B1", name="old.txt"),
            ],
            send_at="+2h",
            content_type="text/markdown",
            keywords=["work"],
            headers={"X-Priority": "1"},
            body="One o'clock?\n\nA",
        )
        text = render_draft(draft, identities=["Alice <a@x.org>"])
        assert "# You can send as: Alice <a@x.org>" in text
        assert parse_draft(text) == draft

    def test_comments_continuations_and_empty_headers(self):
        draft = parse_draft("# note\nTo: a@x.org,\n  b@y.org\nCc:\nAttach:\n\nbody\n\n")
        assert [a.email for a in draft.to] == ["a@x.org", "b@y.org"]
        assert draft.cc == []
        assert draft.attachments == []
        assert draft.body == "body"

    def test_empty_fields_are_left_out(self):
        draft = parse_draft("Send-At:\nX-Empty:\n\nbody")
        assert draft.send_at == ""
        assert draft.headers == {}
        assert "You can send as" not in render_draft(draft)

    def test_headers_only(self):
        assert parse_draft("Subject: hi").body == ""
        assert parse_draft("").subject == ""

    def test_message_ids_with_or_without_brackets(self):
        assert parse_draft("References: <a@x> b@y\n\n").references == ["a@x", "b@y"]

    def test_attachment_options(self):
        draft = parse_draft("Attach: f.txt; Name = a.txt ; type=text/plain; junk\n\n")
        assert draft.attachments == [AttachmentSpec(path="f.txt", name="a.txt", type="text/plain")]

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("hello\n\n", "line 1: 'hello' is not a 'Name: value' header"),
            ("To: bob\n\n", "line 1: To: 'bob' is not an email address"),
            ("Keywords: a b\n\n", "Keywords:"),
            ("Content-Type: text/rtf\n\n", "use one of text/plain"),
            ("Send-At: soon\n\n", r"is not \+2h"),
            ("Send-At: 2020-01-01T00:00:00Z\n\n", "in the past"),
        ],
    )
    def test_errors_say_where(self, text, message):
        with pytest.raises(DraftParseError, match=message):
            parse_draft(text)

    def test_from_an_email(self):
        email = Email.from_wire(
            {
                "from": [{"email": "a@x.org"}],
                "subject": "Draft",
                "keywords": {"$draft": True, "work": True},
                "htmlBody": [{"partId": "2", "type": "text/html"}],
                "textBody": [{"partId": "2", "type": "text/html"}],
                "bodyValues": {"2": {"value": "<p>hi</p>"}},
                "attachments": [
                    {"partId": "3", "blobId": "B3", "name": "f.pdf", "type": "application/pdf"}
                ],
            }
        )
        draft = draft_from_email(email)
        assert draft.keywords == ["work"]
        assert draft.content_type == "text/html"
        assert draft.body == "hi"
        assert draft.attachments == [
            AttachmentSpec(blob_id="B3", name="f.pdf", type="application/pdf")
        ]
        plain = draft_from_email(Email.from_wire({"preview": "p"}))
        assert (plain.content_type, plain.body) == ("text/plain", "p")


class TestCreate:
    def test_plain(self):
        draft = Draft(
            from_=[EmailAddress(email="a@x.org")],
            to=[EmailAddress(email="b@y.org")],
            reply_to=[EmailAddress(email="r@x.org")],
            subject="s",
            in_reply_to=["m1"],
            references=["m1"],
            keywords=["work"],
            headers={"X-Tag": "1"},
            body="hi",
        )
        create = email_create(draft, mailbox_id="d")
        assert create["mailboxIds"] == {"d": True}
        assert create["keywords"] == {"$draft": True, "$seen": True, "work": True}
        assert create["replyTo"] == [{"email": "r@x.org"}]
        assert create["header:X-Tag:asText"] == "1"
        assert create["bodyValues"] == {"text": {"value": "hi"}}
        assert "htmlBody" not in create
        assert "cc" not in create

    def test_markdown_and_html(self):
        markdown = email_create(
            Draft(body="*hi*", content_type="text/markdown"),
            mailbox_id="d",
            html_renderer=lambda text: "<p><em>hi</em></p>",
        )
        assert markdown["bodyValues"] == {
            "text": {"value": "*hi*"},
            "html": {"value": "<p><em>hi</em></p>"},
        }
        html = email_create(Draft(body="<p>a</p>", content_type="text/html"), mailbox_id="d")
        assert html["bodyValues"]["text"] == {"value": "a"}
        with pytest.raises(DraftParseError, match="needs an HTML renderer"):
            email_create(Draft(body="x", content_type="text/markdown"), mailbox_id="d")

    def test_attachments(self):
        draft = Draft(
            body="x",
            attachments=[
                AttachmentSpec(path="dir/a.pdf"),
                AttachmentSpec(path="b.bin", name="named.bin", type="application/x-b"),
                AttachmentSpec(blob_id="B9"),
            ],
        )
        uploaded = {
            "dir/a.pdf": Uploaded("U1", "application/pdf", 10),
            "b.bin": Uploaded("U2", "x/y"),
        }
        parts = email_create(draft, mailbox_id="d", uploaded=uploaded)["attachments"]
        assert parts == [
            {
                "blobId": "U1",
                "type": "application/pdf",
                "disposition": "attachment",
                "name": "a.pdf",
            },
            {
                "blobId": "U2",
                "type": "application/x-b",
                "disposition": "attachment",
                "name": "named.bin",
            },
            {"blobId": "B9", "type": "application/octet-stream", "disposition": "attachment"},
        ]
        with pytest.raises(DraftParseError, match="was not uploaded"):
            email_create(Draft(attachments=[AttachmentSpec(path="c")]), mailbox_id="d")

    def test_checked_before_it_is_sent(self):
        with pytest.raises(InvalidEmailCreateError):
            email_create(Draft(keywords=["a b"]), mailbox_id="d")


class TestSending:
    def test_hold_for_and_until(self):
        assert hold("+90m", now=NOW) == ("HOLDFOR", "5400", 5400.0)
        assert hold("+1d", now=NOW)[2] == 86400.0
        name, value, seconds = hold("2026-10-30T14:00:00+01:00", now=NOW)
        assert (name, value, seconds) == ("HOLDUNTIL", "2026-10-30T13:00:00Z", 3600.0)
        assert hold("2026-10-30T13:00", now=NOW)[1] == "2026-10-30T13:00:00Z"

    def test_envelope_names_every_recipient_once(self):
        draft = Draft(
            to=[EmailAddress(email="b@y.org")],
            cc=[EmailAddress(email="b@y.org"), EmailAddress(name="no address")],
            bcc=[EmailAddress(email="c@z.org")],
            send_at="+1h",
        )
        assert envelope("a@x.org", draft, now=NOW) == {
            "mailFrom": {"email": "a@x.org", "parameters": {"HOLDFOR": "3600"}},
            "rcptTo": [{"email": "b@y.org"}, {"email": "c@z.org"}],
        }
        assert envelope("a@x.org", Draft(), now=NOW) is None

    def test_the_sent_patch(self):
        assert sent_patch(drafts_id="d", sent_id="s") == {
            "keywords/$draft": None,
            "keywords/$seen": True,
            "mailboxIds/d": None,
            "mailboxIds/s": True,
        }
        assert sent_patch(drafts_id="d", sent_id=None) == {
            "keywords/$draft": None,
            "keywords/$seen": True,
        }
        assert "mailboxIds/d" not in sent_patch(drafts_id="d", sent_id="d")
