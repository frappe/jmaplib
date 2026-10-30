"""Mailbox references: id, role, path, name - in that order."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.ids import MailboxIndex

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
