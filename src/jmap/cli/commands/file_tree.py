"""Finding file nodes by path: ``/Projects/notes.txt``, one name at a time.

The server has no path lookup. Each name is a ``FileNode/query`` on the parent
found before it, so a path three names deep takes three requests. The top level
is the nodes with no parent; ``/`` names it.

Stalwart refuses ``"parentId": null`` in a filter, so a query of the top level
leaves that condition out and keeps the nodes with no parent here. That fetches
every node the other conditions match, which for ``ls /`` is all of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from jmap.cli.commands import raise_first
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import wire_of
from jmap.models.files import NODE_DIRECTORY, infer_node_type

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext

Node = dict[str, Any]

#: What every lookup fetches: enough to list, place and download a node.
PROPERTIES: Final = [
    "id",
    "parentId",
    "name",
    "nodeType",
    "blobId",
    "size",
    "type",
    "modified",
    "changed",
    "role",
]


class FileTree:
    """The account's file nodes, looked up by path."""

    def __init__(self, context: CLIContext) -> None:
        self.context = context

    def find(self, path: str) -> Node | None:
        """The node at ``path``; ``None`` for the top level."""
        node: Node | None = None
        parts = names(path)
        for depth, name in enumerate(parts):
            found = self.child(node, name)
            if found is None:
                shown = "/" + "/".join(parts[: depth + 1])
                raise NotFoundError(f"no file or directory {shown}", hint="see 'jmap file ls'")
            node = found
        return node

    def directory(self, path: str) -> Node | None:
        """The directory at ``path``; ``None`` for the top level."""
        node = self.find(path)
        if node is not None and not is_directory(node):
            raise UsageError(f"{path} is not a directory")
        return node

    def place(self, path: str, name: str) -> tuple[str | None, str]:
        """Where ``path`` puts a node called ``name``: its parent id and its name.

        An existing directory, or a path ending in ``/``, takes the node inside
        it under ``name``; any other path names the node itself.
        """
        parts = names(path)
        if path.endswith("/") or not parts:
            return node_id(self.directory(path)), name
        parent = self.directory("/".join(parts[:-1]))
        last = self.child(parent, parts[-1])
        if last is not None and is_directory(last):
            return str(last["id"]), name
        return node_id(parent), parts[-1]

    def child(self, parent: Node | None, name: str) -> Node | None:
        """The node called ``name`` directly inside ``parent``, if there is one."""
        found = self.query({"parentId": node_id(parent), "name": name})
        return found[0] if found else None

    def query(self, condition: dict[str, Any]) -> list[Node]:
        """The nodes ``condition`` matches, with :data:`PROPERTIES`.

        A ``parentId`` of ``None`` means the top level. No condition at all
        means every node in the account.
        """
        top = "parentId" in condition and condition["parentId"] is None
        wanted = {key: value for key, value in condition.items() if value is not None}
        client = self.context.require("FileNode/query", "FileNode/get")
        with client.batch() as batch:
            found = batch.add("FileNode/query", {"filter": wanted} if wanted else {})
            handle = batch.add("FileNode/get", {"ids": found.ref_ids(), "properties": PROPERTIES})
        raise_first(found, handle)
        nodes: list[Node] = wire_of(handle.result)["list"]
        return [node for node in nodes if node.get("parentId") is None] if top else nodes


def names(path: str) -> list[str]:
    """The names along ``path``, without empty ones and ``.``."""
    return [part for part in path.split("/") if part not in {"", "."}]


def node_id(node: Node | None) -> str | None:
    return None if node is None else str(node["id"])


def is_directory(node: Mapping[str, Any]) -> bool:
    return infer_node_type(dict(node)) == NODE_DIRECTORY
