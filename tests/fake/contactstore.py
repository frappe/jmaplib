"""An in-memory contacts store behind the fake server, for driving the contact commands.

Implements enough of RFC 9610, Stalwart's ``ContactCard/parse`` and the legacy
``Contact`` type to be honest about what the commands send. It is test code;
correctness beyond that is not a goal.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import httpx

from jmap.capabilities.contacts import CONTACTS_PARSE_URN, CONTACTS_URN, FASTMAIL_CONTACTS_URN

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

BOOKS: list[dict[str, Any]] = [
    {"id": "b1", "name": "Personal", "isDefault": True, "isSubscribed": True, "sortOrder": 1},
    {"id": "b2", "name": "Work", "isDefault": False, "isSubscribed": True, "sortOrder": 2},
]


def card(
    card_id: str, full: str, address: str, *, book: str = "b1", **extra: Any
) -> dict[str, Any]:
    return {
        "id": card_id,
        "@type": "Card",
        "version": "1.0",
        "uid": f"urn:uuid:{card_id}",
        "addressBookIds": {book: True},
        "name": {"full": full},
        "emails": {"e1": {"address": address}},
        **extra,
    }


class ContactStore:
    """Address books and cards; ``legacy`` serves Fastmail's ``Contact`` type instead."""

    def __init__(self, server: FakeJMAPServer, *, legacy: bool = False) -> None:
        self.server = server
        self.books = {book["id"]: copy.deepcopy(book) for book in BOOKS}
        self.cards: dict[str, dict[str, Any]] = {}
        self.counter = 0
        #: The most blobs one parse, or cards one set, may carry before ``requestTooLarge``.
        self.max_parse: int | None = None
        self.max_set: int | None = None
        #: False for a server that never counts a query's results.
        self.totals = True
        #: Card ids the server refuses to change, with the error type.
        self.refuse: dict[str, str] = {}
        kind = "Contact" if legacy else "ContactCard"
        urns = [FASTMAIL_CONTACTS_URN] if legacy else [CONTACTS_URN, CONTACTS_PARSE_URN]
        for urn in urns:
            server.capabilities[urn] = {}
            server.primary_accounts[urn] = "a"
        server.handle(f"{kind}/get", self.card_get)
        server.handle(f"{kind}/query", self.card_query)
        server.handle(f"{kind}/set", self.card_set)
        if not legacy:
            server.handle("AddressBook/get", self.book_get)
            server.handle("AddressBook/set", self.book_set)
            server.handle("ContactCard/parse", self.parse)
        server.intercept = self.too_large

    def add(self, *cards: dict[str, Any]) -> None:
        for item in cards:
            self.cards[item["id"]] = copy.deepcopy(item)

    # -- address books ----------------------------------------------------- #
    def book_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return {"accountId": "a", "state": "s1", "list": list(self.books.values()), "notFound": []}

    def book_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        for key, book in (args.get("create") or {}).items():
            book_id = self._new_id("b")
            self.books[book_id] = {"id": book_id, "isDefault": False, **book}
            created[key] = {"id": book_id}
        default = args.get("onSuccessSetIsDefault")
        if isinstance(default, str) and default.startswith("#"):
            chosen = created[default[1:]]["id"]
            for book in self.books.values():
                book["isDefault"] = book["id"] == chosen
        for book_id, patch in (args.get("update") or {}).items():
            self.books[book_id].update(patch)
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        for book_id in args.get("destroy") or []:
            inside = [c for c in self.cards.values() if book_id in c["addressBookIds"]]
            if inside and not args.get("onDestroyRemoveContents"):
                not_destroyed[book_id] = {"type": "addressBookHasContents"}
                continue
            for item in inside:
                del self.cards[item["id"]]
            del self.books[book_id]
            destroyed.append(book_id)
        return {
            "accountId": "a",
            "newState": "s2",
            "created": created,
            "updated": dict.fromkeys(args.get("update") or {}),
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    # -- cards ------------------------------------------------------------- #
    def card_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        ids = args.get("ids")
        wanted = list(self.cards) if ids is None else ids
        found = [self.cards[i] for i in wanted if i in self.cards]
        missing = [i for i in wanted if i not in self.cards]
        return {"accountId": "a", "state": "s1", "list": found, "notFound": missing}

    def card_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        conditions = args.get("filter") or {}
        ids = [i for i, item in self.cards.items() if _matches(item, conditions)]
        for comparator in reversed(args.get("sort") or []):
            ids.sort(
                key=lambda i: _sort_key(self.cards[i], comparator["property"]),
                reverse=not comparator.get("isAscending", True),
            )
        position = args.get("position") or 0
        page = ids[position : position + args["limit"]] if "limit" in args else ids[position:]
        result = {"accountId": "a", "queryState": "q1", "ids": page, "position": position}
        if args.get("calculateTotal") and self.totals:
            result["total"] = len(ids)
        return result

    def card_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        not_created: dict[str, Any] = {}
        for key, item in (args.get("create") or {}).items():
            if not item.get("addressBookIds"):
                not_created[key] = {"type": "invalidProperties", "properties": ["addressBookIds"]}
                continue
            card_id = self._new_id("c")
            self.cards[card_id] = {"id": card_id, **item}
            created[key] = {"id": card_id}
        updated: dict[str, Any] = {}
        not_updated: dict[str, Any] = {}
        for card_id, patch in (args.get("update") or {}).items():
            if card_id in self.refuse:
                not_updated[card_id] = {"type": self.refuse[card_id]}
                continue
            for key, value in patch.items():
                if value is None:
                    self.cards[card_id].pop(key, None)
                else:
                    self.cards[card_id][key] = value
            updated[card_id] = None
        destroyed = [i for i in args.get("destroy") or [] if self.cards.pop(i, None)]
        return {
            "accountId": "a",
            "newState": "s2",
            "created": created,
            "notCreated": not_created,
            "updated": updated,
            "notUpdated": not_updated,
            "destroyed": destroyed,
        }

    def parse(self, args: dict[str, Any], server: FakeJMAPServer) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        not_parsable: list[str] = []
        not_found: list[str] = []
        for blob_id in args["blobIds"]:
            if blob_id not in server.blobs:
                not_found.append(blob_id)
                continue
            fields = _vcard_fields(server.blobs[blob_id][0])
            if "FN" not in fields:
                not_parsable.append(blob_id)
                continue
            found: dict[str, Any] = {"@type": "Card", "name": {"full": fields["FN"]}}
            if "EMAIL" in fields:
                found["emails"] = {"e1": {"address": fields["EMAIL"]}}
            parsed[blob_id] = found
        return {
            "accountId": "a",
            "parsed": parsed,
            "notParsable": not_parsable,
            "notFound": not_found,
        }

    def too_large(self, request: httpx.Request) -> httpx.Response | None:
        """Answer ``requestTooLarge`` to a parse or create over the limits."""
        if request.method != "POST" or not request.url.path.rstrip("/").endswith("/jmap"):
            return None
        body = json.loads(request.content)
        name, arguments, call_id = body["methodCalls"][0]
        count = len(arguments.get("blobIds") or arguments.get("create") or ())
        limit = self.max_parse if name == "ContactCard/parse" else self.max_set
        if limit is None or count <= limit:
            return None
        answer = {"methodResponses": [["error", {"type": "requestTooLarge"}, call_id]]}
        return httpx.Response(200, json={**answer, "sessionState": self.server.session_state})

    def _new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}new{self.counter}"


def _matches(item: dict[str, Any], conditions: dict[str, Any]) -> bool:
    text = json.dumps(item).lower()
    if "text" in conditions and conditions["text"].lower() not in text:
        return False
    if "email" in conditions:
        addresses = [e.get("address") for e in (item.get("emails") or {}).values()]
        if conditions["email"] not in addresses:
            return False
    book = conditions.get("inAddressBook")
    return book is None or book in item.get("addressBookIds", {})


def _sort_key(item: dict[str, Any], prop: str) -> str:
    """``name/surname`` sorts on the full name's last word, ``name/given`` on its first."""
    words = str((item.get("name") or {}).get("full", "")).split() or [""]
    if prop == "name/surname":
        return words[-1]
    return words[0] if prop == "name/given" else str(item.get(prop, ""))


def _vcard_fields(data: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in data.decode().splitlines():
        key, _, value = line.partition(":")
        fields.setdefault(key.upper(), value)
    return fields
