"""Turning what a person types into JMAP ids.

A mailbox can be named five ways, tried in this order: ``id:<id>``, an exact id,
a role (``inbox``, ``sent``...), a ``/``-separated path, a unique name. Roles come
before names because names are localised: ``inbox`` finds the inbox on a server
that calls it "Posteingang".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import wire_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from jmap.client import BatchContext, JMAPClient
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
    def queue(cls, batch: BatchContext) -> Handle[Any]:
        """Add the ``Mailbox/get`` to a batch, so it shares a request with other calls."""
        return batch.add("Mailbox/get", {"ids": None, "properties": list(MAILBOX_PROPERTIES)})

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
