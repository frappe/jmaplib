"""The search language: parsing, compiling to RFC 8621 filters, dates, sizes, sort."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from jmap.mail.query import (
    And,
    Not,
    Or,
    QueryError,
    Term,
    mentions,
    needs_snippets,
    parse,
    parse_date,
    parse_size,
    parse_sort,
    to_filter,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def compiled(text: str) -> dict[str, Any]:
    node = parse(text)
    assert node is not None
    return to_filter(node, mailbox_id=lambda name: f"id-{name}", now=NOW)


class TestParse:
    def test_empty(self):
        assert parse("  ") is None

    def test_bare_words_search_text(self):
        assert parse("hello") == Term("text", "hello")

    def test_adjacent_terms_are_anded(self):
        assert parse("from:bob AND is:unread") == And((Term("from", "bob"), Term("is", "unread")))

    def test_or_binds_looser_than_and(self):
        assert parse("a b OR c") == Or(
            (And((Term("text", "a"), Term("text", "b"))), Term("text", "c"))
        )

    def test_not_and_minus(self):
        assert parse("NOT a") == Not(Term("text", "a"))
        assert parse("-from:x") == Not(Term("from", "x"))

    def test_groups(self):
        assert parse("(a OR b)") == Or((Term("text", "a"), Term("text", "b")))

    def test_quotes(self):
        assert parse('subject:"two words"') == Term("subject", "two words")
        assert parse('"from:x"') == Term("text", "from:x")

    def test_aliases(self):
        assert parse("since:today") == Term("after", "today")
        assert parse("until:today keyword:x") == And((Term("before", "today"), Term("tag", "x")))

    def test_a_lone_minus_is_text(self):
        assert parse("-") == Term("text", "-")

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("frm:bob", "did you mean 'from'"),
            ("http://x", 'put it in "quotes"'),
            ("from:", "needs a value"),
            ("(a", "the query ends too early"),
            ("(a b c", "the query ends too early"),
            ("a )", r"unexpected '\)'"),
            ("OR a", "unexpected 'OR'"),
            ('subject:"open', "unbalanced quote"),
            ("(a))", r"unexpected '\)'"),
            ("(a OR", "ends too early"),
        ],
    )
    def test_errors(self, text, message):
        with pytest.raises(QueryError, match=message):
            parse(text)

    def test_a_group_closed_by_something_else(self, monkeypatch):
        # Reached only if a token other than ")" follows a group, which the
        # grammar otherwise prevents.
        from jmap.mail import query

        parser = query._Parser(["(", "a", "OR"])
        monkeypatch.setattr(parser, "_or", lambda: Term("text", "a"))
        with pytest.raises(QueryError, match="never closed"):
            parser._unary()

    def test_query_error_is_a_value_error(self):
        assert issubclass(QueryError, ValueError)


class TestCompile:
    def test_distinct_keys_merge_into_one_condition(self):
        assert compiled("from:bob is:unread has:attachment") == {
            "from": "bob",
            "notKeyword": "$seen",
            "hasAttachment": True,
        }

    def test_repeated_keys_need_an_operator(self):
        assert compiled("from:a from:b") == {
            "operator": "AND",
            "conditions": [{"from": "a"}, {"from": "b"}],
        }

    def test_an_operator_first(self):
        assert compiled("(a OR b) c") == {
            "operator": "AND",
            "conditions": [
                {"text": "c"},
                {"operator": "OR", "conditions": [{"text": "a"}, {"text": "b"}]},
            ],
        }

    def test_two_operators(self):
        assert compiled("(a OR b) (c OR d)")["conditions"][0]["operator"] == "OR"

    def test_not_folds_into_keyword_and_attachment_conditions(self):
        assert compiled("-is:unread") == {"hasKeyword": "$seen"}
        assert compiled("NOT tag:work") == {"notKeyword": "work"}
        assert compiled("-has:attachment") == {"hasAttachment": False}

    def test_not_over_anything_else_is_an_operator(self):
        assert compiled("-from:x") == {"operator": "NOT", "conditions": [{"from": "x"}]}

    def test_mailboxes(self):
        assert compiled("in:inbox notin:junk") == {
            "inMailbox": "id-inbox",
            "inMailboxOtherThan": ["id-junk"],
        }

    def test_dates_go_out_in_utc(self):
        assert compiled("after:2026-01-31") == {"after": "2026-01-31T00:00:00Z"}
        india = timezone(timedelta(hours=5, minutes=30))
        node = parse("before:today")
        assert node is not None
        assert to_filter(node, mailbox_id=str, now=NOW, zone=india) == {
            "before": "2026-10-29T18:30:00Z"
        }

    def test_now_defaults_to_the_clock(self):
        node = parse("after:1d")
        assert node is not None
        assert to_filter(node, mailbox_id=str)["after"].endswith("Z")

    def test_sizes_headers_threads_smime(self):
        assert compiled("larger:1M smaller:2k") == {"minSize": 1048576, "maxSize": 2048}
        assert compiled("header:List-Id") == {"header": ["List-Id"]}
        assert compiled("header:X-Tag=a=b") == {"header": ["X-Tag", "a=b"]}
        assert compiled("thread:some:flagged") == {"someInThreadHaveKeyword": "$flagged"}
        assert compiled("smime:verified") == {"hasVerifiedSmime": True}

    def test_every_is(self):
        for name, condition in {
            "read": {"hasKeyword": "$seen"},
            "flagged": {"hasKeyword": "$flagged"},
            "unflagged": {"notKeyword": "$flagged"},
            "draft": {"hasKeyword": "$draft"},
        }.items():
            assert compiled(f"is:{name}") == condition

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("is:urnead", "did you mean 'unread'"),
            ("has:pictures", "only has:attachment"),
            ("thread:many:x", "use thread:all"),
            ("thread:all", "use thread:all"),
            ("smime:maybe", "use smime:any"),
            ("tag:a%b", "outside the keyword charset"),
            ("before:soon", "is not a date"),
            ("larger:big", "is not a size"),
        ],
    )
    def test_errors(self, text, message):
        with pytest.raises(QueryError, match=message):
            compiled(text)


class TestHelpers:
    def test_snippets_are_for_text_searches(self):
        assert needs_snippets(parse("subject:x OR NOT body:y"))
        assert not needs_snippets(parse("is:unread"))
        assert not needs_snippets(None)

    def test_mentions(self):
        assert mentions(parse("a (in:x OR b)"), "in", "notin")
        assert not mentions(parse("a OR b"), "in")
        assert mentions(parse("-in:x"), "in")

    def test_dates(self):
        assert parse_date("yesterday", now=NOW) == datetime(2026, 10, 29, tzinfo=UTC)
        assert parse_date("2w", now=NOW) == NOW - timedelta(days=14)
        assert parse_date("2026-01-02T03:04:05+01:00", now=NOW).hour == 3

    def test_sizes(self):
        assert parse_size("500") == 500
        assert parse_size("1.5KB") == 1536
        assert parse_size("1g") == 1024**3

    def test_sort(self):
        assert parse_sort("-date, subject,,") == [
            {"property": "receivedAt", "isAscending": False},
            {"property": "subject", "isAscending": True},
        ]
        assert parse_sort("flagged,-unread") == [
            {"property": "hasKeyword", "keyword": "$flagged", "isAscending": False},
            {"property": "hasKeyword", "keyword": "$seen", "isAscending": False},
        ]
        with pytest.raises(QueryError, match="cannot sort by 'colour'"):
            parse_sort("colour")
