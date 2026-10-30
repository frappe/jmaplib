"""Turning what a person types into JMAP ids.

A mailbox can be named five ways, tried in this order: ``id:<id>``, an exact id,
a role (``inbox``, ``sent``...), a ``/``-separated path, a unique name. Roles come
before names because names are localised: ``inbox`` finds the inbox on a server
that calls it "Posteingang".
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import wire_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from jmap.client import BatchContext, JMAPClient
    from jmap.core.ids import Id
    from jmap.core.invocation import Handle

#: What every mailbox listing and reference needs, and nothing slower to compute.
MAILBOX_PROPERTIES: Final = (
    "id",
    "name",
    "parentId",
    "role",
    "sortOrder",
    "totalEmails",
    "unreadEmails",
    "myRights",
    "isSubscribed",
)

#: Deeper than any real hierarchy; stops a server's parent cycle from looping.
_MAX_DEPTH: Final = 64

Mailbox = Mapping[str, Any]


class MailboxIndex:
    """Every mailbox in one account, from one ``Mailbox/get``."""

    def __init__(self, mailboxes: Iterable[Mailbox]) -> None:
        self._by_id: dict[str, Mailbox] = {str(box["id"]): box for box in mailboxes}

    @classmethod
    def queue(cls, batch: BatchContext, *, account_id: Id | None = None) -> Handle[Any]:
        """Add the ``Mailbox/get`` to a batch, so it shares a request with other calls."""
        arguments = {"ids": None, "properties": list(MAILBOX_PROPERTIES)}
        return batch.add("Mailbox/get", arguments, account_id=account_id)

    @classmethod
    def from_handle(cls, handle: Handle[Any]) -> MailboxIndex:
        return cls(wire_of(handle.result)["list"])

    @classmethod
    def load(cls, client: JMAPClient) -> MailboxIndex:
        with client.batch() as batch:
            handle = cls.queue(batch)
        return cls.from_handle(handle)

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, mailbox_id: str) -> Mailbox | None:
        return self._by_id.get(mailbox_id)

    def resolve(self, reference: str) -> Mailbox:
        """The one mailbox ``reference`` names, or a :class:`NotFoundError`."""
        if reference.startswith("id:"):
            return self._exact(reference[3:], reference)
        if reference in self._by_id:
            return self._by_id[reference]
        wanted = reference.lower()
        for finder in (self._by_role, self._by_path, self._by_name):
            found = finder(wanted)
            if len(found) == 1:
                return found[0]
            if found:
                raise _ambiguous(reference, [self.path_of(str(box["id"])) for box in found])
        raise _missing(reference)

    def path_of(self, mailbox_id: str) -> str:
        """``Archive/2025``: names from the top of the hierarchy down."""
        names: list[str] = []
        current = self._by_id.get(mailbox_id)
        while current is not None and len(names) < _MAX_DEPTH:
            names.append(str(current.get("name") or ""))
            parent = current.get("parentId")
            current = self._by_id.get(str(parent)) if parent else None
        return "/".join(reversed(names))

    def depth_of(self, mailbox_id: str) -> int:
        return self.path_of(mailbox_id).count("/")

    def ordered(self) -> list[Mailbox]:
        """Parents before children, siblings by ``sortOrder`` then name."""
        children: dict[str | None, list[Mailbox]] = {}
        for box in self._by_id.values():
            parent = box.get("parentId")
            key = str(parent) if parent in self._by_id else None
            children.setdefault(key, []).append(box)
        ordered: list[Mailbox] = []
        pending = list(reversed(_sorted(children.get(None, []))))
        while pending:
            box = pending.pop()
            ordered.append(box)
            pending += reversed(_sorted(children.get(str(box["id"]), [])))
        return ordered

    def _exact(self, mailbox_id: str, reference: str) -> Mailbox:
        found = self._by_id.get(mailbox_id)
        if found is None:
            raise _missing(reference)
        return found

    def _by_role(self, wanted: str) -> list[Mailbox]:
        return [box for box in self._by_id.values() if str(box.get("role") or "") == wanted]

    def _by_path(self, wanted: str) -> list[Mailbox]:
        wanted = wanted.strip("/")
        return [box for key, box in self._by_id.items() if self.path_of(key).lower() == wanted]

    def _by_name(self, wanted: str) -> list[Mailbox]:
        return [box for box in self._by_id.values() if str(box.get("name") or "").lower() == wanted]


def _sorted(boxes: Sequence[Mailbox]) -> list[Mailbox]:
    return sorted(boxes, key=lambda box: (box.get("sortOrder") or 0, str(box.get("name") or "")))


def _missing(reference: str) -> NotFoundError:
    return NotFoundError(f"no mailbox {reference!r}", hint="see 'jmap mailbox list'")


def _ambiguous(reference: str, paths: Sequence[str]) -> UsageError:
    return UsageError(
        f"{reference!r} names several mailboxes: {', '.join(sorted(paths))}",
        hint="use the full path or id:<id>",
    )


# --------------------------------------------------------------------------- #
# Message references and the cursor
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Ref:
    """One message reference: a listing number, an id, a Message-ID or a thread."""

    kind: Literal["index", "id", "msgid", "thread"]
    value: str


_PREFIXES: Final[tuple[Literal["id", "msgid", "thread"], ...]] = ("id", "msgid", "thread")
_RANGE: Final = re.compile(r"(\d+)(?:-(\d+))?")
#: More than any listing holds; stops ``1-99999999`` building a huge list.
_MAX_RANGE: Final = 10_000


def parse_refs(words: Sequence[str]) -> list[Ref]:
    """``3``, ``1-5``, ``2,7``, ``id:M1``, ``msgid:<a@b>``, ``thread:T1``, or a bare id.

    Bare numbers are listing numbers. RFC 8620 §1.2 asks servers not to use
    all-digit ids, which is what keeps that unambiguous; ``id:`` forces an id.
    """
    refs: list[Ref] = []
    for word in words:
        for prefix in _PREFIXES:
            if word.startswith(f"{prefix}:") and len(word) > len(prefix) + 1:
                refs.append(Ref(prefix, word[len(prefix) + 1 :]))
                break
        else:
            refs += _numbers(word) if word[:1].isdigit() else [Ref("id", word)]
    if not refs:
        raise UsageError("name at least one message")
    return refs


def _numbers(word: str) -> list[Ref]:
    refs: list[Ref] = []
    for part in filter(None, word.split(",")):
        found = _RANGE.fullmatch(part)
        if found is None:
            raise UsageError(f"{part!r} is not a message number or range")
        first, last = int(found[1]), int(found[2] or found[1])
        if first < 1 or last < first or last - first >= _MAX_RANGE:
            raise UsageError(f"{part!r} is not a usable range")
        refs += [Ref("index", str(number)) for number in range(first, last + 1)]
    return refs


@dataclass(frozen=True, slots=True)
class Listing:
    """What the last listing showed, so ``read 3`` means its third row."""

    ids: tuple[str, ...]
    #: The number shown on the first row: ``26`` on the second page of 25.
    first: int = 1
    #: The mailbox listed, when there was one; a move takes mail out of it.
    mailbox_id: str | None = None
    query: str = ""


class Cursor:
    """The last listing, in memory and (when given a path) on disk between runs."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._listing: Listing | None = None

    def record(self, listing: Listing) -> None:
        self._listing = listing
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = {**asdict(listing), "ids": list(listing.ids), "at": int(time.time())}
        self._path.write_text(json.dumps(body), encoding="utf-8")

    @property
    def listing(self) -> Listing | None:
        if self._listing is None and self._path is not None:
            self._listing = _read_listing(self._path)
        return self._listing

    def resolve(self, number: int) -> str:
        listing = self.listing
        if listing is None:
            raise NotFoundError(
                f"there is no listing to take message {number} from",
                hint="run 'jmap mail list' first",
            )
        index = number - listing.first
        if not 0 <= index < len(listing.ids):
            last = listing.first + len(listing.ids) - 1
            shown = f"{listing.first}-{last}" if listing.ids else "none"
            raise NotFoundError(
                f"the last listing has no message {number} (it showed {shown})",
                hint="run 'jmap mail list' again",
            )
        return listing.ids[index]


def _read_listing(path: Path) -> Listing | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return Listing(
            ids=tuple(str(item) for item in body["ids"]),
            first=int(body["first"]),
            mailbox_id=body.get("mailbox_id"),
            query=str(body.get("query", "")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None  # a missing or damaged cursor is no listing, not an error
