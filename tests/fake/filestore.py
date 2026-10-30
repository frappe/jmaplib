"""An in-memory file tree behind the fake server, for driving the file commands.

Implements enough of draft-ietf-jmap-filenode to be honest about what the
commands send: queries by parent and name, ``depth``, creation references in
``parentId``, the inferred node type, ``onExists`` and ``onDestroyRemoveChildren``.
It is test code; correctness beyond that is not a goal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jmap.capabilities.files import FILENODE_URN
from jmap.models.files import infer_node_type

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


class FileStore:
    """File nodes, answering the FileNode methods of ``server``."""

    def __init__(self, server: FakeJMAPServer) -> None:
        self.server = server
        self.nodes: dict[str, dict[str, Any]] = {}
        self.last_set: dict[str, Any] = {}
        self._created = 0
        server.capabilities[FILENODE_URN] = {}
        server.primary_accounts[FILENODE_URN] = "a"
        server.accounts["a"]["accountCapabilities"] = {FILENODE_URN: {}}
        for name, handler in {
            "FileNode/query": self.query,
            "FileNode/get": self.get,
            "FileNode/set": self.set,
        }.items():
            server.handle(name, handler)

    def add(self, node_id: str, name: str, parent: str | None = None, **extra: Any) -> None:
        self.nodes[node_id] = {"id": node_id, "parentId": parent, "name": name, **extra}
        self.nodes[node_id]["nodeType"] = infer_node_type(self.nodes[node_id])

    def file(self, node_id: str, name: str, parent: str | None, content: bytes) -> None:
        blob_id = self.server.store_blob(content, "text/plain")
        self.add(node_id, name, parent, blobId=blob_id, size=len(content), type="text/plain")

    def query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        condition = args["filter"]
        parents = {condition["parentId"]}
        found: list[str] = []
        for _ in range(args.get("depth", 1)):
            level = [key for key, node in self.nodes.items() if node["parentId"] in parents]
            found += level
            parents = set(level)
        if "name" in condition:
            found = [key for key in found if self.nodes[key]["name"] == condition["name"]]
        return {
            "accountId": args["accountId"],
            "queryState": "q",
            "canCalculateChanges": False,
            "position": 0,
            "ids": found,
        }

    def get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        found = [dict(self.nodes[key]) for key in args["ids"] if key in self.nodes]
        return {"accountId": args["accountId"], "state": "s", "list": found, "notFound": []}

    def set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        self.last_set = args
        created: dict[str, Any] = {}
        not_created: dict[str, Any] = {}
        for key, node in (args.get("create") or {}).items():
            parent = node.get("parentId")
            if parent and parent.startswith("#"):
                parent = created[parent[1:]]["id"]
            clash = self._named(parent, node["name"])
            if clash is not None and args.get("onExists") != "replace":
                not_created[key] = {"type": "alreadyExists", "existingId": clash}
                continue
            if clash is not None:
                del self.nodes[clash]
            new_id = self._new_id()
            self.add(
                new_id,
                node["name"],
                parent,
                **{k: v for k, v in node.items() if k not in {"name", "parentId"}},
            )
            created[key] = {"id": new_id}
        for key, patch in (args.get("update") or {}).items():
            self.nodes[key].update(patch)
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        for key in args.get("destroy") or []:
            children = [k for k, node in self.nodes.items() if node["parentId"] == key]
            if children and not args.get("onDestroyRemoveChildren"):
                not_destroyed[key] = {"type": "nodeHasChildren"}
                continue
            self._remove(key)
            destroyed.append(key)
        return {
            "accountId": args["accountId"],
            "created": created,
            "notCreated": not_created,
            "updated": dict.fromkeys(args.get("update") or {}),
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    def path_of(self, node_id: str) -> str:
        node = self.nodes[node_id]
        above = "" if node["parentId"] is None else self.path_of(node["parentId"])
        return f"{above}/{node['name']}"

    def _named(self, parent: str | None, name: str) -> str | None:
        return next(
            (k for k, n in self.nodes.items() if n["parentId"] == parent and n["name"] == name),
            None,
        )

    def _remove(self, node_id: str) -> None:
        for key in [k for k, node in self.nodes.items() if node["parentId"] == node_id]:
            self._remove(key)
        del self.nodes[node_id]

    def _new_id(self) -> str:
        self._created += 1
        return f"n{self._created}"
