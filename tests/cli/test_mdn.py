"""``jmap mdn``: sending read receipts and reading the ones that come back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.fake.mailstore import MailStore, email

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

MDN_URN = "urn:ietf:params:jmap:mdn"


class Receipts:
    """``MDN/send`` and ``MDN/parse``, remembering what was sent."""

    def __init__(self, server: FakeJMAPServer, store: MailStore) -> None:
        self.store = store
        self.sent: list[dict[str, Any]] = []
        #: Emails whose receipt the server refuses.
        self.refuse: set[str] = set()
        server.handle("MDN/send", self.send)
        server.handle("MDN/parse", self.parse)

    def send(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        self.sent.append(args)
        sent: dict[str, Any] = {}
        not_sent: dict[str, Any] = {}
        for key, receipt in args["send"].items():
            if receipt["forEmailId"] in self.refuse:
                not_sent[key] = {"type": "forbiddenFrom"}
                continue
            self.store.emails[receipt["forEmailId"]]["keywords"]["$mdnsent"] = True
            sent[key] = {"finalRecipient": "rfc822; alice@example.com"}
        return {"accountId": args["accountId"], "sent": sent, "notSent": not_sent}

    def parse(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        parsed = {
            blob: {
                "forEmailId": "e1",
                "subject": "Read: Invoice",
                "finalRecipient": "rfc822; bob@example.com",
                "disposition": {
                    "actionMode": "manual-action",
                    "sendingMode": "mdn-sent-manually",
                    "type": "displayed",
                },
            }
            for blob in args["blobIds"]
            if blob.startswith("blob-r")
        }
        return {
            "accountId": args["accountId"],
            "parsed": parsed,
            "notParsable": [b for b in args["blobIds"] if b.startswith("blob-e")],
            "notFound": [b for b in args["blobIds"] if not b.startswith("blob-")],
        }


@pytest.fixture(autouse=True)
def receipts(server: FakeJMAPServer, mailer: MailStore) -> Receipts:
    server.capabilities[MDN_URN] = {}
    server.primary_accounts[MDN_URN] = "a"
    first = email("e1", "Invoice")
    first["to"] = [{"email": "alice@work.example"}]
    mailer.add(
        first,
        email("e2", "Done", keywords=("$mdnsent",)),
        email("r1", "Read: Invoice"),
    )
    return Receipts(server, mailer)


class TestSend:
    def test_one_receipt(self, invoke, receipts, mailer):
        result = invoke("mdn", "send", "e1")
        assert result.code == 0, result.err
        assert "sent 1 read receipt(s)" in result.err
        [call] = receipts.sent
        assert call["identityId"] == "i2"  # the identity the message was sent to
        assert call["send"]["r1"] == {
            "forEmailId": "e1",
            "disposition": {
                "actionMode": "manual-action",
                "sendingMode": "mdn-sent-manually",
                "type": "displayed",
            },
            "includeOriginalMessage": False,
        }
        assert call["onSuccessUpdateEmail"] == {"#r1": {"keywords/$mdnsent": True}}
        assert mailer.emails["e1"]["keywords"]["$mdnsent"] is True

    def test_options(self, invoke, receipts):
        result = invoke(
            "mdn",
            "send",
            "e1",
            "--type",
            "processed",
            "--auto",
            "--include-original",
            "--subject",
            "Got it",
            "--body",
            "Thanks",
            "--from",
            "alice@example.com",
        )
        assert result.code == 0, result.err
        [call] = receipts.sent
        assert call["identityId"] == "i1"
        assert call["send"]["r1"]["subject"] == "Got it"
        assert call["send"]["r1"]["textBody"] == "Thanks"
        assert call["send"]["r1"]["includeOriginalMessage"] is True
        assert call["send"]["r1"]["disposition"] == {
            "actionMode": "automatic-action",
            "sendingMode": "mdn-sent-automatically",
            "type": "processed",
        }

    def test_an_acknowledged_message_is_skipped(self, invoke, receipts):
        result = invoke("mdn", "send", "e2")
        assert result.code == 0
        assert "was sent already" in result.err
        assert receipts.sent == []

    def test_json(self, invoke):
        result = invoke("mdn", "send", "e1", "--json")
        assert result.json()["sent"] == {"r1": {"finalRecipient": "rfc822; alice@example.com"}}

    def test_a_refused_receipt(self, invoke, receipts):
        receipts.refuse.add("e1")
        result = invoke("mdn", "send", "e1")
        assert result.code == 8
        assert "1 of 1 not sent: r1: forbiddenFrom" in result.err

    def test_an_unknown_type(self, invoke):
        result = invoke("mdn", "send", "e1", "--type", "read")
        assert result.code == 2
        assert "displayed" in result.err

    def test_an_unknown_identity(self, invoke):
        result = invoke("mdn", "send", "e1", "--from", "eve@example.com")
        assert result.code == 9
        assert "eve@example.com" in result.err

    def test_a_missing_message(self, invoke, receipts):
        result = invoke("mdn", "send", "nope")
        assert result.code == 9
        assert receipts.sent == []

    def test_without_the_capability(self, invoke, server):
        del server.capabilities[MDN_URN]
        assert invoke("mdn", "send", "e1").code == 7


class TestParse:
    def test_a_message(self, invoke):
        result = invoke("mdn", "parse", "r1")
        assert result.code == 0, result.err
        assert result.out.split("\t") == [
            "e1",
            "displayed",
            "manual-action",
            "rfc822; bob@example.com",
            "Read: Invoice\n",
        ]

    def test_blobs_as_json(self, invoke):
        result = invoke("mdn", "parse", "blob:blob-r1", "--json")
        assert [item["forEmailId"] for item in result.json()] == ["e1"]

    def test_unusable_ones_are_noted(self, invoke):
        result = invoke("mdn", "parse", "r1", "e2", "blob:gone")
        assert result.code == 0
        assert "blob-e2: not a read receipt" in result.err
        assert "gone: not found" in result.err

    def test_nothing_usable(self, invoke):
        result = invoke("mdn", "parse", "e2")
        assert result.code == 9


def test_no_disposition(invoke, server):
    server.respond("MDN/parse", {"accountId": "a", "parsed": {"x": {"forEmailId": None}}})
    result = invoke("mdn", "parse", "blob:x")
    assert result.out == "\t\t\t\t\n"
