"""The mail batch recipes, end to end against the in-memory mail store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from jmap.auth import BasicAuth
from jmap.client import JMAPClient
from jmap.mail.ops import (
    LIST_PROPERTIES,
    queue_import_and_send,
    queue_list,
    queue_read,
    queue_send,
    queue_thread,
)

from .mailstore import MailStore, email, mail_server

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@dataclass
class Mail:
    client: JMAPClient
    store: MailStore
    server: FakeJMAPServer

    def calls(self) -> list[Any]:
        made: list[Any] = self.server.requests[-1]["methodCalls"]
        return made


@pytest.fixture
def mail() -> Mail:
    server, store = mail_server()
    store.add(
        email("e1", "Invoice", received="2026-10-01T09:00:00Z", thread="t1"),
        email(
            "e2", "Re: Invoice", received="2026-10-02T09:00:00Z", thread="t1", keywords=("$seen",)
        ),
        email("e3", "Lunch", received="2026-10-03T09:00:00Z", mailboxes=("arch",)),
    )
    client = JMAPClient.connect(
        "https://jmap.example.com/.well-known/jmap",
        auth=BasicAuth("alice@example.com", "pw"),
        http=httpx.Client(**server.client_kwargs()),
    )
    return Mail(client, store, server)


class TestList:
    def test_one_request_with_the_ids_chained(self, mail):
        client = mail.client
        with client.batch() as batch:
            found = queue_list(
                batch,
                filter={"inMailbox": "inbox"},
                sort=[{"property": "receivedAt", "isAscending": False}],
                limit=1,
            )
        assert found.query.result.ids == ["e2"]
        assert found.query.result.total == 2
        assert [e.subject for e in found.emails.result.items] == ["Re: Invoice"]
        assert found.snippets is None
        names = [call[0] for call in mail.calls()]
        assert names == ["Email/query", "Email/get"]

    def test_defaults_send_no_filter_sort_or_limit(self, mail):
        client = mail.client
        with client.batch() as batch:
            found = queue_list(batch, calculate_total=False, collapse_threads=True)
        query = mail.calls()[0][1]
        assert isinstance(query, dict)
        assert "filter" not in query
        assert "sort" not in query
        assert "limit" not in query
        # One message per thread: e1 and e2 share one.
        assert len(found.query.result.ids) == 2
        assert "e3" in found.query.result.ids

    def test_snippets_share_the_filter(self, mail):
        client = mail.client
        with client.batch() as batch:
            found = queue_list(batch, filter={"text": "Invoice"}, snippets=True)
        assert found.snippets is not None
        subjects = {item["subject"] for item in found.snippets.result["list"]}
        assert subjects == {"<mark>Invoice</mark>", "Re: <mark>Invoice</mark>"}
        with client.batch() as batch:
            unfiltered = queue_list(batch, snippets=True)
        assert unfiltered.snippets is not None
        assert "filter" not in mail.calls()[2][1]


class TestRead:
    def test_bodies_headers_and_marking_seen(self, mail):
        client, store = mail.client, mail.store
        with client.batch() as batch:
            read = queue_read(batch, ["e1"], headers=["List-Id"], all_headers=True)
        [item] = read.emails.result.items
        assert "$seen" not in (item.keywords or {})
        assert item.header("header:List-Id:asText") == "<list.example.com>"
        assert item.body_values["1"].value == "Hello there."
        assert read.seen is not None
        assert read.seen.result.updated == {"e1": None}
        assert "$seen" in store.emails["e1"]["keywords"]
        get = mail.calls()[0][1]
        assert isinstance(get, dict)
        assert get["fetchTextBodyValues"] is True
        assert "headers" in get["properties"]

    def test_without_marking(self, mail):
        client, store = mail.client, mail.store
        with client.batch() as batch:
            read = queue_read(batch, ["e1"], mark_seen=False)
        assert read.seen is None
        assert "$seen" not in store.emails["e1"]["keywords"]
        with client.batch() as batch:
            nothing = queue_read(batch, [])
        assert nothing.seen is None


def test_a_thread_in_one_request(mail):
    client = mail.client
    with client.batch() as batch:
        thread = queue_thread(batch, "e2", properties=["subject"])
    assert thread.start.result.items[0].thread_id == "t1"
    assert thread.thread.result.items[0].email_ids == ["e1", "e2"]
    assert [e.subject for e in thread.emails.result.items] == ["Invoice", "Re: Invoice"]
    assert len(mail.calls()) == 3
    assert LIST_PROPERTIES[0] == "id"


class TestSend:
    def test_create_and_submit_in_one_request(self, mail):
        client, store = mail.client, mail.store
        create = {
            "mailboxIds": {"drafts": True},
            "to": [{"email": "bob@example.com"}],
            "subject": "Hi",
            "textBody": [{"partId": "t", "type": "text/plain"}],
            "bodyValues": {"t": {"value": "Hello"}},
        }
        with client.batch() as batch:
            sent = queue_send(
                batch,
                create,
                identity_id="i1",
                envelope={"mailFrom": {"email": "alice@example.com"}, "rcptTo": []},
                on_success={"keywords/$draft": None},
                update={"e1": {"keywords/$answered": True}},
            )
        email_id = sent.email.result.created["draft"].id
        assert sent.submission.result.created["send"].id
        assert store.emails["e1"]["keywords"]["$answered"] is True
        calls = mail.calls()
        assert calls[1][1]["create"]["send"]["emailId"] == "#draft"
        assert calls[1][1]["onSuccessUpdateEmail"] == {"#send": {"keywords/$draft": None}}
        assert store.submissions[next(iter(store.submissions))]["emailId"] == email_id

    def test_without_extras(self, mail):
        with mail.client.batch() as batch:
            queue_send(batch, {"mailboxIds": {"drafts": True}}, identity_id="i1")
        email_call, submission_call = mail.calls()
        assert "update" not in email_call[1]
        assert "envelope" not in submission_call[1]["create"]["send"]
        assert "onSuccessUpdateEmail" not in submission_call[1]

    def test_import_and_send(self, mail):
        mail.server.blobs["raw"] = (b"Subject: Raw\r\n\r\nbody", "message/rfc822")
        with mail.client.batch() as batch:
            sent = queue_import_and_send(
                batch,
                "raw",
                mailbox_id="drafts",
                identity_id="i1",
                on_success={"keywords/$seen": True},
            )
        assert sent.email.result["created"]["message"]["id"]
        assert mail.calls()[1][1]["create"]["send"]["emailId"] == "#message"
