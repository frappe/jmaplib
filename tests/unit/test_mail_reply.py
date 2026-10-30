"""Replies, forwards, and choosing the identity to send from."""

from __future__ import annotations

from typing import Any

from jmap.mail.compose import AttachmentSpec
from jmap.mail.reply import forward_draft, pick_identity, reply_draft
from jmap.models.mail.objects import Email, Identity

ALICE = Identity(id="i1", name="Alice", email="alice@x.org")
WORK = Identity(id="i2", email="alice@work.org")


def original(**wire: Any) -> Email:
    return Email.from_wire(
        {
            "messageId": ["m1@x"],
            "references": ["m0@x"],
            "from": [{"name": "Bob", "email": "bob@y.org"}],
            "to": [{"email": "alice@work.org"}, {"email": "carol@z.org"}],
            "cc": [{"email": "dave@z.org"}, {"email": "CAROL@z.org"}],
            "subject": "Lunch",
            "sentAt": "2026-10-29T10:00:00Z",
            "blobId": "BLOB",
            "attachments": [
                {"partId": "3", "blobId": "B3", "name": "menu.pdf", "type": "application/pdf"},
                {"partId": "4"},
            ],
            **wire,
        }
    )


class TestReply:
    def test_to_the_sender_threaded_and_quoted(self):
        draft = reply_draft(original(), identity=ALICE, quote_text="One?\n\nTwo?")
        assert [a.email for a in draft.from_] == ["alice@x.org"]
        assert [a.email for a in draft.to] == ["bob@y.org"]
        assert draft.cc == []
        assert draft.subject == "Re: Lunch"
        assert draft.in_reply_to == ["m1@x"]
        assert draft.references == ["m0@x", "m1@x"]
        assert (
            draft.body == "\n\nOn 2026-10-29T10:00:00Z, Bob <bob@y.org> wrote:\n> One?\n>\n> Two?\n"
        )

    def test_reply_all_leaves_out_me_and_duplicates(self):
        draft = reply_draft(original(), identity=WORK, reply_all=True, me=["dave@z.org"])
        assert [a.email for a in draft.cc] == ["carol@z.org"]

    def test_reply_to_wins_and_re_is_not_repeated(self):
        draft = reply_draft(
            original(
                replyTo=[{"email": "list@y.org"}],
                subject="RE: Lunch",
                sentAt=None,
                receivedAt=None,
                **{"from": None},
            ),
            identity=None,
            quote_text="x",
        )
        assert [a.email for a in draft.to] == ["list@y.org"]
        assert draft.subject == "RE: Lunch"
        assert draft.from_ == []
        assert draft.body.startswith("\n\nsomeone wrote:")

    def test_without_quoting(self):
        assert reply_draft(original(), identity=ALICE).body == ""


class TestForward:
    def test_inline_with_the_attachments(self):
        draft = forward_draft(original(), identity=ALICE, quote_text="Hello")
        assert draft.subject == "Fwd: Lunch"
        assert draft.to == []
        assert draft.attachments == [
            AttachmentSpec(blob_id="B3", name="menu.pdf", type="application/pdf")
        ]
        assert "---------- Forwarded message ----------" in draft.body
        assert "Cc: dave@z.org, CAROL@z.org" in draft.body
        assert draft.body.rstrip().endswith("Hello")

    def test_as_an_eml_attachment(self):
        draft = forward_draft(original(subject="Fw: Lunch"), identity=ALICE, as_attachment=True)
        assert draft.subject == "Fw: Lunch"
        assert draft.attachments == [
            AttachmentSpec(blob_id="BLOB", name="Fw: Lunch.eml", type="message/rfc822")
        ]
        assert draft.body == ""

    def test_minimal_original(self):
        draft = forward_draft(Email.from_wire({}), identity=None)
        assert draft.subject == "Fwd: "
        assert "Cc:" not in draft.body
        named = forward_draft(Email.from_wire({}), identity=None, as_attachment=True)
        assert named.attachments[0].name == "message.eml"


class TestPickIdentity:
    def test_wanted_by_address_or_id(self):
        assert pick_identity([ALICE, WORK], wanted="ALICE@work.org") is WORK
        assert pick_identity([ALICE, WORK], wanted="i1") is ALICE
        assert pick_identity([ALICE, WORK], wanted="nobody@x") is None

    def test_the_address_the_original_was_sent_to(self):
        assert pick_identity([ALICE, WORK], original=original()) is WORK
        assert pick_identity([ALICE], original=original(), username="nobody") is ALICE

    def test_then_the_username_then_the_first(self):
        assert pick_identity([ALICE, WORK], username="alice@work.org") is WORK
        assert pick_identity([ALICE, WORK], username="nobody") is ALICE
        assert pick_identity([]) is None
