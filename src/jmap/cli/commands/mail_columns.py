"""The columns of a message listing, and the line under it."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

from rich.markup import escape

from jmap.cli import format as fmt
from jmap.cli.errors import UsageError
from jmap.cli.ids import Mailbox
from jmap.cli.output import Column

#: What ``--columns`` can name, in the order the help lists them.
COLUMNS: Final = ("flags", "date", "from", "to", "subject", "preview", "size", "id", "thread")


class ListingColumns:
    """The columns of one listing, highlighting search matches where there are snippets."""

    def __init__(self, *, table: bool, snippets: dict[str, dict[str, Any]]) -> None:
        self.table = table
        self.snippets = snippets
        self._builders: dict[str, Callable[[], Column]] = {
            "flags": lambda: Column("FLAGS", "keywords", fmt.flags),
            "date": self._date,
            "from": lambda: _address("from"),
            "to": lambda: _address("to"),
            "subject": lambda: self._snippet("SUBJECT", "subject"),
            "preview": lambda: self._snippet("PREVIEW", "preview"),
            "size": self._size,
            "id": lambda: Column("ID", "id"),
            "thread": lambda: Column("THREAD", "threadId"),
        }

    def default(self, listed: Mailbox | None) -> list[Column]:
        """Flags, date, sender (recipient in Sent and Drafts), subject, matches, size."""
        outgoing = listed is not None and listed.get("role") in {"sent", "drafts"}
        columns = [self.column(name) for name in ("flags", "date", "subject")]
        columns.insert(2, self.column("to" if outgoing else "from"))
        if self.snippets:
            columns.append(self._snippet("MATCH", "preview"))
        columns.append(self.column("size"))
        return columns

    def named(self, text: str) -> list[Column]:
        """The columns ``text`` lists, comma separated, in its order."""
        names = [name.strip().lower() for name in text.split(",") if name.strip()]
        unknown = [name for name in names if name not in self._builders]
        if unknown or not names:
            problem = f"no column named {', '.join(unknown)}" if unknown else "--columns is empty"
            raise UsageError(problem, hint=f"choose from {', '.join(COLUMNS)}")
        return [self.column(name) for name in names]

    def column(self, name: str) -> Column:
        return self._builders[name]()

    def _date(self) -> Column:
        """People's dates in a table; the server's ISO dates in plain text."""
        if not self.table:
            return Column("DATE", "receivedAt")
        now = datetime.now().astimezone()
        return Column("DATE", "receivedAt", lambda row: fmt.when(row.get("receivedAt"), now=now))

    def _size(self) -> Column:
        if not self.table:
            return Column("SIZE", "size", justify="right")
        return Column("SIZE", "size", lambda row: fmt.size(row.get("size")), "right")

    def _snippet(self, header: str, key: str) -> Column:
        """``key`` with search matches highlighted where the server sent a snippet."""
        snippets = self.snippets

        def snippet(row: Mapping[str, Any]) -> str | None:
            found: str | None = (snippets.get(row["id"]) or {}).get(key)
            return found

        def plain(row: Mapping[str, Any]) -> str:
            found = snippet(row)
            return fmt.snippet_plain(found) if found else str(row.get(key) or "")

        def markup(row: Mapping[str, Any]) -> str:
            found = snippet(row)
            return fmt.snippet_markup(found) if found else escape(str(row.get(key) or ""))

        return Column(header, key, plain, markup=markup)


def footer(
    rows: list[dict[str, Any]], first: int, total: int | None, listed: Mailbox | None, sort: str
) -> str:
    """``1-25 of 312 · Inbox · -date``."""
    shown = f"{first}-{first + len(rows) - 1}" if rows else "no messages"
    parts = [shown + (f" of {total}" if rows and total is not None else "")]
    if listed:
        parts.append(str(listed.get("name")))
    parts.append(sort)
    return " · ".join(parts)


def _address(key: str) -> Column:
    return Column(key.upper(), key, lambda row: fmt.address(row.get(key)))
