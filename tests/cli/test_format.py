"""Cell text: flags, dates, sizes, addresses, snippets."""

from __future__ import annotations

from datetime import UTC, datetime

from jmap.cli import format as fmt

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def test_flags():
    assert fmt.flags({}) == "N"
    assert fmt.flags({"keywords": {"$seen": True, "$flagged": True}, "hasAttachment": True}) == "*@"
    assert fmt.flags({"keywords": {"$answered": True, "$draft": True, "$junk": True}}) == "NRDJ"
    assert fmt.flags({"keywords": {"$seen": True, "$forwarded": True}}) == "F"


def test_when():
    assert fmt.when("2026-10-30T09:05:00Z", now=NOW) == "09:05"
    assert fmt.when("2026-03-01T09:05:00Z", now=NOW) == "Mar 01"
    assert fmt.when("2025-03-01T09:05:00Z", now=NOW) == "2025-03-01"
    assert fmt.when(None, now=NOW) == ""
    assert fmt.when("someday", now=NOW) == "someday"


def test_size():
    assert fmt.size(None) == ""
    assert fmt.size(340) == "340B"
    assert fmt.size(1536) == "1.5K"
    assert fmt.size(3 * 1024**2) == "3.0M"
    assert fmt.size(5 * 1024**4) == "5120.0G"


def test_addresses():
    people = [{"name": "Bob", "email": "bob@x"}, {"email": "c@x"}]
    assert fmt.address(people) == "Bob +1"
    assert fmt.address([{"email": "c@x"}]) == "c@x"
    assert fmt.address([{}]) == ""
    assert fmt.address(None) == ""
    assert fmt.address_list(people) == "Bob <bob@x>, c@x"
    assert fmt.address_list(None) == ""


def test_snippets():
    text = "a &amp; <mark>[b]</mark>"
    assert fmt.snippet_markup(text) == "a & [bold underline]\\[b][/]"
    assert fmt.snippet_markup(None) == ""
    assert fmt.snippet_plain(text) == "a & [b]"
    assert fmt.snippet_plain(None) == ""
