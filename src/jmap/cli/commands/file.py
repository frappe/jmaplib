"""``jmap file``: files and directories stored on the server (FileNode)."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from jmap.cli import format as fmt
from jmap.cli.commands import cli, report_set
from jmap.cli.commands.file_tree import FileTree, Node, is_directory, names, node_id
from jmap.cli.context import CLIContext
from jmap.cli.errors import UsageError
from jmap.cli.files import safe_name, target_path, write_file
from jmap.cli.output import Column, View

app: typer.Typer = typer.Typer(no_args_is_help=True)

RemotePath = Annotated[str, typer.Argument(help="A path on the server: /Projects/notes.txt.")]
Replace = Annotated[bool, typer.Option("--replace", help="Replace a file of the same name.")]

#: How deep ``file tree`` goes unless told.
_TREE_DEPTH: Final = 10


@app.command()
def ls(ctx: typer.Context, path: Annotated[str, typer.Argument(help="A directory.")] = "/") -> None:
    """List a directory, directories first."""
    tree = FileTree(cli(ctx))
    node = tree.find(path)
    if node is not None and not is_directory(node):
        nodes = [node]
    else:
        nodes = sorted(tree.query({"parentId": node_id(node)}), key=_by_kind_and_name)
    tree.context.renderer.render(View(nodes, _columns(_label)))


@app.command("tree")
def tree_(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Where to start.")] = "/",
    depth: Annotated[int, typer.Option(help="How many levels down.")] = _TREE_DEPTH,
) -> None:
    """Show a directory and everything below it, indented."""
    tree = FileTree(cli(ctx))
    top = node_id(tree.directory(path))
    # Every node, arranged here: Stalwart accepts the draft's "depth" but does not recurse.
    nodes = tree.query({})
    levels: dict[str, int] = {}
    ordered = _depth_first(nodes, top, depth, levels)
    tree.context.renderer.render(
        View(ordered, _columns(lambda node: "  " * levels[node["id"]] + _label(node)))
    )


@app.command()
def upload(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="The local file.")],
    target: Annotated[
        str, typer.Argument(help="Where: a directory, or a new path. Default: the top level.")
    ] = "/",
    replace: Replace = False,
) -> None:
    """Upload a file."""
    context = cli(ctx)
    try:
        data = source.expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {source}: {error.strerror or error}") from error
    parent, name = FileTree(context).place(target, source.name)
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    client = context.require("FileNode/set")
    blob = client.upload(data, content_type=media_type)
    # Always a blobId: a node created without one is a directory.
    node = {"parentId": parent, "name": name, "blobId": blob.blob_id, "type": media_type}
    _set(context, {"create": {"file": node}}, f"uploaded {name}", replace=replace)


@app.command()
def download(
    ctx: typer.Context,
    path: RemotePath,
    directory: Annotated[Path, typer.Option("--dir", help="Save it here.")] = Path(),
    save: Annotated[str | None, typer.Option(help="Save it to this file, or - for stdout.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace a file that exists.")] = False,
) -> None:
    """Download a file; a name already taken gets a number, unless --force."""
    context = cli(ctx)
    node = FileTree(context).find(path)
    if node is None or not node.get("blobId"):
        raise UsageError(f"{path} is not a file")
    name = safe_name(node.get("name"), "download")
    data = context.client().download(
        str(node["blobId"]), name=name, content_type=node.get("type") or "application/octet-stream"
    )
    if save == "-":
        context.write_bytes(data)
        return
    target = Path(save) if save else target_path(directory, name, force=force)
    write_file(target, data, force=force or save is None)
    context.renderer.note(f"saved {target}")


@app.command()
def mkdir(
    ctx: typer.Context,
    path: RemotePath,
    parents: Annotated[
        bool, typer.Option("--parents", "-p", help="Create missing parents too.")
    ] = False,
) -> None:
    """Create a directory."""
    context = cli(ctx)
    tree = FileTree(context)
    parent: Node | None = None
    parts = names(path)
    for index, name in enumerate(parts):
        found = tree.child(parent, name)
        if found is None:
            _create_directories(context, parent, parts[index:], parents=parents)
            return
        if not is_directory(found):
            raise UsageError(f"/{'/'.join(parts[: index + 1])} is a file")
        parent = found
    if not parents:
        raise UsageError(f"{path} already exists")


@app.command()
def rm(
    ctx: typer.Context,
    path: RemotePath,
    recursive: Annotated[
        bool, typer.Option("--recursive", "-r", help="Delete a directory and all inside it.")
    ] = False,
) -> None:
    """Delete a file or an empty directory, after you confirm."""
    context = cli(ctx)
    node = FileTree(context).find(path)
    if node is None:
        raise UsageError("the top level cannot be deleted")
    context.confirm(f"Delete {path}{' and everything in it' if recursive else ''}?")
    arguments: dict[str, Any] = {"destroy": [node["id"]]}
    if recursive:
        arguments["onDestroyRemoveChildren"] = True
    _set(context, arguments, f"deleted {path}")


@app.command()
def mv(
    ctx: typer.Context,
    source: RemotePath,
    target: Annotated[str, typer.Argument(help="A directory to move it into, or a new path.")],
    replace: Replace = False,
) -> None:
    """Move or rename a file or directory."""
    context = cli(ctx)
    tree = FileTree(context)
    node = tree.find(source)
    if node is None:
        raise UsageError("the top level cannot be moved")
    parent, name = tree.place(target, str(node.get("name")))
    patch = {"parentId": parent, "name": name}
    _set(context, {"update": {node["id"]: patch}}, f"moved {source} to {target}", replace=replace)


def _create_directories(
    context: CLIContext, parent: Node | None, missing: list[str], *, parents: bool
) -> None:
    """Create ``missing`` inside ``parent``, each inside the one before, in one request."""
    if len(missing) > 1 and not parents:
        raise UsageError(f"there is no directory {missing[0]}", hint="pass --parents to create it")
    create: dict[str, Any] = {}
    above = node_id(parent)
    for number, name in enumerate(missing, 1):
        # No blobId: that is what makes it a directory.
        create[f"d{number}"] = {"parentId": above, "name": name}
        above = f"#d{number}"
    _set(
        context,
        {"create": create},
        f"created {len(missing)} director{'ies' if len(missing) > 1 else 'y'}",
    )


def _set(
    context: CLIContext, arguments: dict[str, Any], done: str, *, replace: bool = False
) -> None:
    if replace:
        arguments["onExists"] = "replace"
    client = context.require("FileNode/set")
    with client.batch() as batch:
        handle = batch.add("FileNode/set", arguments)
    report_set(context, handle.result, done)


def _columns(name: Callable[[Mapping[str, Any]], str]) -> list[Column]:
    return [
        Column("SIZE", "size", lambda n: fmt.size(n.get("size")), justify="right"),
        Column("MODIFIED", "modified", lambda n: str(n.get("modified") or n.get("changed") or "")),
        Column("NAME", "name", name),
        Column("ID", "id"),
    ]


def _label(node: Mapping[str, Any]) -> str:
    return str(node.get("name") or "") + ("/" if is_directory(node) else "")


def _by_kind_and_name(node: Node) -> tuple[bool, str]:
    return not is_directory(node), str(node.get("name") or "").casefold()


def _depth_first(
    nodes: list[Node], parent: str | None, depth: int, levels: dict[str, int], level: int = 0
) -> list[Node]:
    """``nodes`` below ``parent``, each followed by its own children, ``depth`` levels down."""
    if level >= depth:
        return []
    ordered: list[Node] = []
    inside = sorted(
        (node for node in nodes if node.get("parentId") == parent), key=_by_kind_and_name
    )
    for node in inside:
        levels[node["id"]] = level
        ordered += [node, *_depth_first(nodes, node["id"], depth, levels, level + 1)]
    return ordered
