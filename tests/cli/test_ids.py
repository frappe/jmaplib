"""Mailbox references: id, role, path, name - in that order."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.ids import Cursor, Listing, MailboxIndex, parse_refs

from .conftest import MAILBOXES

if TYPE_CHECKING:
    from collections.abc import Mapping


def index(*extra: Mapping[str, Any]) -> MailboxIndex:
    return MailboxIndex([*MAILBOXES, *extra])


class TestResolve:
    def test_forced_id(self):
        assert index().resolve("id:m3")["name"] == "2025"
        with pytest.raises(NotFoundError, match="no mailbox 'id:inbox'"):
            index().resolve("id:inbox")

    def test_exact_id(self):
        assert index().resolve("m2")["role"] == "archive"

    def test_role_before_name(self):
        renamed = {"id": "x", "name": "inbox", "parentId": None}
        assert index(renamed).resolve("INBOX")["id"] == "m1"

    def test_path_ignores_case_and_slashes(self):
        assert index().resolve("archive/2025/")["id"] == "m3"

    def test_unique_name(self):
        assert index().resolve("2025")["id"] == "m3"

    def test_an_ambiguous_name(self):
        twin = {"id": "m4", "name": "2025", "parentId": "m1"}
        with pytest.raises(UsageError, match=r"Archive/2025, Inbox/2025") as caught:
            index(twin).resolve("2025")
        assert caught.value.hint == "use the full path or id:<id>"

    def test_missing(self):
        with pytest.raises(NotFoundError) as caught:
            index().resolve("Receipts")
        assert caught.value.hint == "see 'jmap mailbox list'"


class TestTree:
    def test_paths_and_depth(self):
        boxes = index()
        assert boxes.path_of("m3") == "Archive/2025"
        assert boxes.depth_of("m3") == 1
        assert boxes.path_of("nope") == ""

    def test_a_parent_cycle_stops(self):
        cycle = MailboxIndex(
            [{"id": "a", "name": "A", "parentId": "b"}, {"id": "b", "name": "B", "parentId": "a"}]
        )
        assert cycle.path_of("a").count("/") == 63

    def test_order_is_parents_first_then_sort_order(self):
        orphan = {"id": "o", "name": "Orphan", "parentId": "gone", "sortOrder": 0}
        boxes = index(orphan)
        assert [box["id"] for box in boxes.ordered()] == ["o", "m1", "m2", "m3"]
        assert len(boxes) == 4
        assert boxes.get("m1") is not None
        assert boxes.get("zz") is None


class TestParseRefs:
    def test_numbers_ranges_and_lists(self):
        refs = parse_refs(["3", "1-2,7"])
        assert [(ref.kind, ref.value) for ref in refs] == [
            ("index", "3"),
            ("index", "1"),
            ("index", "2"),
            ("index", "7"),
        ]

    def test_prefixes_and_bare_ids(self):
        refs = parse_refs(["id:42", "msgid:<a@b>", "thread:T1", "Mabc", "id:"])
        assert [(ref.kind, ref.value) for ref in refs] == [
            ("id", "42"),
            ("msgid", "<a@b>"),
            ("thread", "T1"),
            ("id", "Mabc"),
            ("id", "id:"),
        ]

    @pytest.mark.parametrize("word", ["3-1", "0", "1-x", "1-20000"])
    def test_bad_ranges(self, word):
        with pytest.raises(UsageError):
            parse_refs([word])

    def test_nothing(self):
        with pytest.raises(UsageError, match="name at least one message"):
            parse_refs([])


class TestCursor:
    def test_on_disk_between_runs(self, tmp_path):
        path = tmp_path / "a" / "cursor.json"
        Cursor(path).record(Listing(ids=("e1", "e2"), first=26, mailbox_id="inbox", query="q"))
        again = Cursor(path)
        assert again.resolve(27) == "e2"
        assert again.listing == Listing(("e1", "e2"), 26, "inbox", "q")

    def test_in_memory(self):
        cursor = Cursor(None)
        cursor.record(Listing(ids=("e1",)))
        assert cursor.resolve(1) == "e1"

    def test_no_listing(self, tmp_path):
        with pytest.raises(NotFoundError, match="no listing") as caught:
            Cursor(tmp_path / "none.json").resolve(1)
        assert caught.value.hint == "run 'jmap mail list' first"

    def test_out_of_range(self):
        cursor = Cursor(None)
        cursor.record(Listing(ids=("e1", "e2")))
        with pytest.raises(NotFoundError, match=r"no message 3 \(it showed 1-2\)"):
            cursor.resolve(3)
        cursor.record(Listing(ids=()))
        with pytest.raises(NotFoundError, match=r"it showed none"):
            cursor.resolve(1)

    @pytest.mark.parametrize("body", ["{", '{"ids": "x"}', "[]", '{"ids": []}'])
    def test_a_damaged_file_is_no_listing(self, tmp_path, body):
        path = tmp_path / "cursor.json"
        path.write_text(body)
        assert Cursor(path).listing is None
