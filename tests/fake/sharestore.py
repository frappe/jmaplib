"""Shareable objects, principals and share notifications behind the fake server.

Enough of RFC 9670 to be honest about what the sharing commands send: ``/set``
patches are applied pointer by pointer, so a patch that rewrote the whole
``shareWith`` map would show up as lost entries. It is test code.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

PRINCIPALS_URN = "urn:ietf:params:jmap:principals"
OWNER_URN = "urn:ietf:params:jmap:principals:owner"
CALENDARS_URN = "urn:ietf:params:jmap:calendars"
CONTACTS_URN = "urn:ietf:params:jmap:contacts"
FILENODE_URN = "urn:ietf:params:jmap:filenode"

PRINCIPALS: list[dict[str, Any]] = [
    {"id": "alice", "type": "individual", "name": "Alice", "email": "alice@example.com"},
    {"id": "bob", "type": "individual", "name": "Bob", "email": "bob@example.com"},
    {"id": "carol", "type": "individual", "name": "Carol", "email": "carol@example.com"},
    {"id": "team", "type": "group", "name": "Team Bob", "email": None},
]

NOTIFICATIONS: list[dict[str, Any]] = [
    {
        "id": "n1",
        "created": "2026-10-01T09:00:00Z",
        "changedBy": {"name": "Bob", "email": "bob@example.com", "principalId": "bob"},
        "objectType": "Mailbox",
        "objectAccountId": "b",
        "objectId": "bm1",
        "oldRights": None,
        "newRights": {"mayReadItems": True, "maySetSeen": True},
        "name": "Bob's inbox",
    },
    {
        "id": "n2",
        "created": "2026-10-02T09:00:00Z",
        "changedBy": {"name": None, "email": "carol@example.com", "principalId": None},
        "objectType": "Calendar",
        "objectAccountId": "c",
        "objectId": "cc1",
        "oldRights": {"mayReadItems": True, "mayWriteAll": False},
        "newRights": {"mayReadItems": False, "mayWriteAll": True},
        "name": "Carol's calendar",
    },
    {
        "id": "n3",
        "created": "2026-10-03T09:00:00Z",
        "changedBy": None,
        "objectType": "AddressBook",
        "objectAccountId": "c",
        "objectId": "cb1",
        "oldRights": {"mayRead": True},
        "newRights": None,
        "name": "Carol's contacts",
    },
]


def _objects() -> dict[str, dict[str, dict[str, Any]]]:
    shared = {"bob": {"mayReadItems": True, "maySetSeen": True}}
    return {
        "Mailbox": {
            "m1": {"id": "m1", "name": "Inbox", "role": "inbox", "parentId": None},
            "m2": {"id": "m2", "name": "Team", "role": None, "parentId": None, "shareWith": shared},
        },
        "Calendar": {"c1": {"id": "c1", "name": "Work", "shareWith": None}},
        "AddressBook": {"b1": {"id": "b1", "name": "Friends", "shareWith": {"carol": {}}}},
        "FileNode": {"f1": {"id": "f1", "name": "docs", "shareWith": None}},
    }


class ShareStore:
    """Answers ``Foo/get``/``Foo/set`` for the four shareable types, and the principals."""

    def __init__(self, server: FakeJMAPServer) -> None:
        self.objects = _objects()
        self.principals = {item["id"]: dict(item) for item in PRINCIPALS}
        self.notifications = {item["id"]: copy.deepcopy(item) for item in NOTIFICATIONS}
        for type_name in self.objects:
            server.handle(f"{type_name}/get", self._getter(type_name))
            server.handle(f"{type_name}/set", self._setter(type_name))
        server.handle("Principal/get", self.principal_get)
        server.handle("Principal/query", self.principal_query)
        server.handle("ShareNotification/query", self.notification_query)
        server.handle("ShareNotification/get", self.notification_get)
        server.handle("ShareNotification/set", self.notification_set)

    def _getter(self, type_name: str) -> Any:
        def get(args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
            stored = self.objects[type_name]
            return _got(args, stored, args.get("ids"))

        return get

    def _setter(self, type_name: str) -> Any:
        def set_(args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
            stored = self.objects[type_name]
            updated: dict[str, Any] = {}
            not_updated: dict[str, Any] = {}
            for key, patch in (args.get("update") or {}).items():
                if key not in stored:
                    not_updated[key] = {"type": "notFound"}
                    continue
                _apply(stored[key], patch)
                updated[key] = None
            return {"accountId": args["accountId"], "updated": updated, "notUpdated": not_updated}

        return set_

    def principal_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return _got(args, self.principals, args.get("ids"))

    def principal_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        condition: dict[str, Any] = args.get("filter") or {}
        text = str(condition.get("text", "")).lower()
        ids = [
            key
            for key, item in self.principals.items()
            if text in f"{item['name']} {item['email']}".lower()
            and condition.get("type") in {None, item["type"]}
        ]
        return {"accountId": args["accountId"], "queryState": "q", "ids": ids, "position": 0}

    def notification_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        ids = sorted(self.notifications, reverse=bool(args.get("sort")))
        return {"accountId": args["accountId"], "queryState": "q", "ids": ids, "position": 0}

    def notification_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return _got(args, self.notifications, args.get("ids"))

    def notification_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        destroyed = [key for key in args.get("destroy") or [] if key in self.notifications]
        for key in destroyed:
            del self.notifications[key]
        return {"accountId": args["accountId"], "destroyed": destroyed}


def _got(
    args: dict[str, Any], stored: dict[str, dict[str, Any]], ids: list[str] | None
) -> dict[str, Any]:
    wanted = args.get("properties")
    found = list(stored.values()) if ids is None else [stored[k] for k in ids if k in stored]
    items = [
        {key: copy.deepcopy(value) for key, value in item.items() if not wanted or key in wanted}
        | {"id": item["id"]}
        for item in found
    ]
    missing = [key for key in ids or [] if key not in stored]
    return {"accountId": args["accountId"], "state": "s", "list": items, "notFound": missing}


def _apply(item: dict[str, Any], patch: dict[str, Any]) -> None:
    """Apply a patch one pointer at a token depth of two at most, as RFC 8620 §5.3 says."""
    for key, value in patch.items():
        name, _, sub = key.partition("/")
        if not sub:
            item[name] = value
        elif value is None:
            (item.get(name) or {}).pop(sub, None)
        else:
            item[name] = {**(item.get(name) or {}), sub: value}


def share_server(server: FakeJMAPServer) -> ShareStore:
    """Advertise principals and the shareable types on ``server``, then serve them."""
    for urn in (PRINCIPALS_URN, CALENDARS_URN, CONTACTS_URN, FILENODE_URN):
        server.capabilities[urn] = {}
        server.primary_accounts[urn] = "a"
    server.accounts["a"]["accountCapabilities"] = {
        PRINCIPALS_URN: {"currentUserPrincipalId": "alice"},
        OWNER_URN: {"accountIdForPrincipal": "a", "principalId": "alice"},
    }
    return ShareStore(server)
