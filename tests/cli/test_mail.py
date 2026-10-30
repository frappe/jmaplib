"""``jmap mail list`` and ``search``: filters, pages, the cursor and references."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from jmap.capabilities.registry import ActiveCapabilities
from jmap.cli.commands.mail import combine, query_text
from jmap.mail.query import And, Term
from tests.fake.mailstore import email

if TYPE_CHECKING:
    from .conftest import Result

pytestmark = pytest.mark.usefixtures("account")


@pytest.fixture(autouse=True)
def mail(store):
    store.add(
        email(
            "e1",
            "Invoice for October",
            received="2026-10-28T09:00:00Z",
            attachment=True,
            thread="t1",
        ),
        email(
            "e2",
            "Re: Invoice for October",
            sender="carol@example.com",
            received="2026-10-29T10:00:00Z",
            keywords=("$seen", "$flagged"),
            thread="t1",
        ),
        email("e3", "Lunch?", received="2026-10-30T08:00:00Z", mailboxes=("arch",)),
        email("e4", "Report", received="2026-10-27T08:00:00Z", mailboxes=("sent",)),
    )
    return store


def lines(result: Result) -> list[list[str]]:
    assert result.code == 0, result.err
    return [line.split("\t") for line in result.out.splitlines()]


class TestList:
    def test_the_inbox_newest_first_and_numbered(self, invoke):
        result = invoke("mail", "list")
        rows = lines(result)
        assert [row[0] for row in rows] == ["1", "2"]
        assert rows[0][1:] == [
            "*",
            "2026-10-29T10:00:00Z",
            "Carol",
            "Re: Invoice for October",
            "1012",
        ]
        assert rows[1][1] == "N@"
        assert "1-2 of 2 · Inbox · -date" in result.err

    def test_ls_is_the_same_command(self, invoke):
        assert invoke("ls").out == invoke("mail", "list").out

    def test_another_mailbox_and_sent_shows_recipients(self, invoke):
        assert lines(invoke("ls", "--in", "archive"))[0][4] == "Lunch?"
        assert lines(invoke("ls", "--in", "sent"))[0][3] == "Alice"

    def test_filters_are_search_terms(self, invoke, server):
        rows = lines(
            invoke("ls", "--unread", "--attachment", "--from", "bob", "--before", "2026-11-01")
        )
        assert [row[4] for row in rows] == ["Invoice for October"]
        query = server.requests[-1]["methodCalls"][0][1]
        assert query["filter"] == {
            "notKeyword": "$seen",
            "hasAttachment": True,
            "from": "bob",
            "before": "2026-11-01T00:00:00Z",
            "inMailbox": "inbox",
        }

    def test_words_and_an_in_term_replace_the_inbox(self, invoke):
        rows = lines(invoke("ls", "in:archive", "OR", "is:flagged"))
        assert {row[4] for row in rows} == {"Lunch?", "Re: Invoice for October"}

    def test_every_mailbox(self, invoke):
        assert len(lines(invoke("ls", "-A"))) == 4

    def test_pages_number_on(self, invoke):
        rows = lines(invoke("ls", "-A", "-n", "2", "--page", "2", "--sort", "date"))
        assert [(row[0], row[4]) for row in rows] == [
            ("3", "Re: Invoice for October"),
            ("4", "Lunch?"),
        ]
        assert invoke("read", "4", "--json").json()["id"] == "e3"

    def test_all_pages(self, invoke, monkeypatch):
        from jmap.cli.commands import mail

        monkeypatch.setattr(mail, "ALL_PAGE", 3)
        result = invoke("ls", "-A", "--all")
        assert len(lines(result)) == 4
        assert "1-4 of 4" in result.err

    def test_count(self, invoke):
        assert invoke("ls", "-A", "--count").out == "4\n"

    def test_threads(self, invoke):
        assert len(lines(invoke("ls", "--threads"))) == 1

    def test_an_empty_mailbox(self, invoke):
        result = invoke("ls", "--in", "junk")
        assert result.out == ""
        assert "no messages · Junk" in result.err

    def test_json_is_wire_objects_in_order(self, invoke):
        items = invoke("ls", "--json").json()
        assert [item["id"] for item in items] == ["e2", "e1"]
        assert "bodyValues" not in items[0]

    def test_table(self, invoke):
        out = invoke("ls", terminal=True).out
        assert "Oct 29" in out
        assert "1012B" in out

    def test_a_bad_query_is_a_usage_error(self, invoke):
        result = invoke("ls", "frm:x")
        assert result.code == 2
        assert "did you mean 'from'" in result.err

    def test_an_unknown_mailbox(self, invoke):
        assert invoke("ls", "--in", "nowhere").code == 9


class TestSearch:
    def test_everywhere_with_snippets(self, invoke, server):
        result = invoke("search", "Invoice", terminal=True)
        assert result.code == 0
        assert "MATCH" in result.out
        assert "a & b" in result.out
        names = [call[0] for call in server.requests[-1]["methodCalls"]]
        assert names == ["Email/query", "Email/get", "SearchSnippet/get"]
        assert "no messages" not in result.err

    def test_plain_snippets(self, invoke):
        rows = lines(invoke("mail", "search", "Invoice"))
        assert rows[0][4] == "Re: Invoice for October"
        assert rows[0][5] == "a & b"

    def test_no_snippets_without_text(self, invoke, server):
        lines(invoke("search", "is:flagged"))
        assert len(server.requests[-1]["methodCalls"]) == 2

    def test_no_snippets_when_the_server_lacks_them(self, invoke, server, monkeypatch):
        real = ActiveCapabilities.supports
        monkeypatch.setattr(
            ActiveCapabilities,
            "supports",
            lambda self, name: name != "SearchSnippet/get" and real(self, name),
        )
        lines(invoke("search", "Invoice"))
        assert len(server.requests[-1]["methodCalls"]) == 2

    def test_a_failed_snippet_call_fails_the_command(self, invoke, server):
        server.fail("SearchSnippet/get", "unsupportedFilter")
        assert invoke("search", "Invoice").code == 6

    def test_a_whole_quoted_query(self, invoke):
        rows = lines(invoke("search", "from:carol OR subject:lunch"))
        assert {row[4] for row in rows} == {"Re: Invoice for October", "Lunch?"}

    def test_in_terms_load_the_mailboxes(self, invoke):
        assert len(lines(invoke("search", "in:sent"))) == 1


class TestReferences:
    def test_numbers_come_from_the_last_listing(self, invoke):
        invoke("ls")
        assert invoke("read", "2", "--json").json()["id"] == "e1"

    def test_ids_message_ids_and_threads(self, invoke):
        assert invoke("read", "e3", "--json").json()["id"] == "e3"
        assert invoke("read", "msgid:<e3@example.com>", "--json").json()["id"] == "e3"
        items = invoke("read", "thread:t1", "e1", "--json").json()
        assert [item["id"] for item in items] == ["e1", "e2"]

    def test_unknown_message_ids_and_threads(self, invoke):
        assert invoke("read", "msgid:<nope@x>").code == 9
        assert invoke("read", "thread:nope").code == 9

    def test_a_bad_id(self, invoke):
        assert invoke("read", "id:a b").code == 2


def test_query_text_keeps_quoted_values_together():
    assert query_text(["subject:two words", "is:unread"]) == 'subject:"two words" is:unread'
    assert query_text(["from:a OR from:b"]) == "from:a OR from:b"
    assert query_text(["hello world"]) == "hello world"


def test_combine():
    assert combine(None, []) is None
    assert combine(None, [Term("a", "1")]) == Term("a", "1")
    both = And((Term("a", "1"), Term("b", "2")))
    assert combine(both, [Term("c", "3")]) == And((*both.items, Term("c", "3")))
