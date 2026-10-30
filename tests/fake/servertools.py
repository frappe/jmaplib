"""Sieve scripts, quotas, push subscriptions and blob lookups behind the fake server.

Test code for the server-tool commands. Each store registers its handlers on a
``FakeJMAPServer`` and keeps the state a test asserts on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

BLOB_URN = "urn:ietf:params:jmap:blob"
SIEVE_URN = "urn:ietf:params:jmap:sieve"
QUOTA_URN = "urn:ietf:params:jmap:quota"
VAPID_URN = "urn:ietf:params:jmap:webpush-vapid"

QUOTAS: list[dict[str, Any]] = [
    {
        "id": "q1",
        "name": "Mail storage",
        "scope": "account",
        "resourceType": "octets",
        "types": ["Mail"],
        "used": 2048,
        "hardLimit": 1048576,
        "warnLimit": 900000,
    },
    {
        "id": "q2",
        "name": "Messages",
        "scope": "account",
        "resourceType": "count",
        "types": ["Mail"],
        "used": 12,
        "hardLimit": None,
    },
]


class SieveStore:
    """Scripts by id; a script is valid unless its text contains ``error``."""

    def __init__(self, server: FakeJMAPServer) -> None:
        self.server = server
        self.scripts: dict[str, dict[str, Any]] = {}
        self._created = 0
        server.capabilities[SIEVE_URN] = {}
        server.primary_accounts[SIEVE_URN] = "a"
        server.handle("SieveScript/get", self.get)
        server.handle("SieveScript/set", self.set)
        server.handle("SieveScript/validate", self.validate)

    def add(self, name: str, text: str, *, active: bool = False) -> str:
        self._created += 1
        script_id = f"s{self._created}"
        blob_id = self.server.store_blob(text.encode(), "application/sieve")
        self.scripts[script_id] = {
            "id": script_id,
            "name": name,
            "blobId": blob_id,
            "isActive": active,
        }
        return script_id

    def text(self, script_id: str) -> str:
        return self.server.blobs[self.scripts[script_id]["blobId"]][0].decode()

    def get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return {"accountId": args["accountId"], "state": "1", "list": list(self.scripts.values())}

    def set(self, args: dict[str, Any], server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        updated: dict[str, Any] = {}
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        if args.get("onSuccessDeactivateScript"):
            for script in self.scripts.values():
                if script["isActive"]:
                    script["isActive"] = False
                    updated[script["id"]] = {"isActive": False}
        for key, script in (args.get("create") or {}).items():
            text = server.blobs[script["blobId"]][0].decode()
            new_id = self.add(script["name"], text)
            server.created_ids[key] = new_id
            created[key] = {"id": new_id, "isActive": False}
        for script_id, patch in (args.get("update") or {}).items():
            self.scripts[script_id].update(patch)
            updated[script_id] = None
        for script_id in args.get("destroy") or []:
            if self.scripts[script_id]["isActive"]:
                not_destroyed[script_id] = {"type": "sieveIsActive"}
                continue
            del self.scripts[script_id]
            destroyed.append(script_id)
        activate = args.get("onSuccessActivateScript")
        if activate:
            target = server.resolve_blob_id(activate)
            for script in self.scripts.values():
                script["isActive"] = script["id"] == target
            updated[target] = {"isActive": True}
        return {
            "accountId": args["accountId"],
            "created": created,
            "updated": updated,
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    def validate(self, args: dict[str, Any], server: FakeJMAPServer) -> dict[str, Any]:
        text = server.blobs[args["blobId"]][0].decode()
        error = (
            {"type": "invalidSieve", "description": "line 1: unknown command"}
            if "error" in text
            else None
        )
        return {"accountId": args["accountId"], "error": error}


class PushStore:
    """Push subscriptions by id."""

    def __init__(self, server: FakeJMAPServer) -> None:
        self.subscriptions: dict[str, dict[str, Any]] = {}
        server.handle("PushSubscription/get", self.get)
        server.handle("PushSubscription/set", self.set)

    def get(self, _args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        shown = [
            {key: value for key, value in item.items() if key not in {"url", "keys"}}
            for item in self.subscriptions.values()
        ]
        return {"list": shown, "notFound": []}

    def set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        not_created: dict[str, Any] = {}
        updated: dict[str, Any] = {}
        destroyed: list[str] = []
        for key, item in (args.get("create") or {}).items():
            if "forbidden" in item["url"]:
                not_created[key] = {"type": "forbidden"}
                continue
            new_id = f"P{len(self.subscriptions) + 1}"
            self.subscriptions[new_id] = {"id": new_id, "verificationCode": None, **item}
            created[key] = {"id": new_id, "expires": item.get("expires")}
        for key, patch in (args.get("update") or {}).items():
            self.subscriptions[key].update(patch)
            updated[key] = None
        not_destroyed: dict[str, Any] = {}
        for key in args.get("destroy") or []:
            if self.subscriptions.pop(key, None) is None:
                not_destroyed[key] = {"type": "notFound"}
                continue
            destroyed.append(key)
        return {
            "created": created,
            "notCreated": not_created,
            "updated": updated,
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }


def serve_quotas(server: FakeJMAPServer) -> None:
    server.capabilities[QUOTA_URN] = {}
    server.primary_accounts[QUOTA_URN] = "a"
    server.handle(
        "Quota/get",
        lambda args, _server: {"accountId": args["accountId"], "state": "1", "list": QUOTAS},
    )


def serve_blob_methods(server: FakeJMAPServer, *, type_names: list[str]) -> None:
    """``Blob/get``, ``Blob/lookup`` and ``Blob/copy``, which keeps the id."""
    server.capabilities[BLOB_URN] = {
        "supportedDigestAlgorithms": ["sha", "sha-256"],
        "supportedTypeNames": type_names,
    }
    server.primary_accounts[BLOB_URN] = "a"

    def lookup(args: dict[str, Any], fake: FakeJMAPServer) -> dict[str, Any]:
        found = [blob for blob in args["ids"] if blob in fake.blobs]
        return {
            "accountId": args["accountId"],
            "list": [
                {
                    "id": blob,
                    "matchedIds": {name: [f"{name[0]}-{blob}"] for name in args["typeNames"]},
                }
                for blob in found
            ],
            "notFound": [blob for blob in args["ids"] if blob not in found],
        }

    def copy(args: dict[str, Any], fake: FakeJMAPServer) -> dict[str, Any]:
        copied = {blob: f"{blob}-copy" for blob in args["blobIds"] if blob in fake.blobs}
        missing = {blob: {"type": "blobNotFound"} for blob in args["blobIds"] if blob not in copied}
        return {
            "fromAccountId": args["fromAccountId"],
            "accountId": args["accountId"],
            "copied": copied or None,
            "notCopied": missing or None,
        }

    server.handle("Blob/lookup", lookup)
    server.handle("Blob/copy", copy)
