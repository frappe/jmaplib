"""``jmap blob``: raw binary data on the server, by id.

``upload`` and ``download`` use the session's HTTP endpoints, which every
server has. ``info``, ``lookup`` and ``copy`` are method calls: the first two
need RFC 9404's ``urn:ietf:params:jmap:blob``, ``copy`` is RFC 8620's own.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap.capabilities.blob import BLOB_URN, BlobCapability
from jmap.cli import format as fmt
from jmap.cli.commands import cli, comma_list
from jmap.cli.errors import NotFoundError, PartialFailureError, UnsupportedError, UsageError
from jmap.cli.files import write_file
from jmap.cli.output import Column, View, wire_of

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient

app: typer.Typer = typer.Typer(no_args_is_help=True)

Blobs = Annotated[list[str], typer.Argument(help="Blob ids.")]


@app.command()
def upload(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="A file, or - for stdin.")],
    content_type: Annotated[
        str | None, typer.Option("--type", help="Media type; guessed from the name if not given.")
    ] = None,
) -> None:
    """Upload a file and print its blob id."""
    context = cli(ctx)
    data = read_source(context, source)
    guessed = mimetypes.guess_type(source)[0] if source != "-" else None
    uploaded = context.client().upload(data, content_type=content_type or guessed)
    columns = [Column("Blob", "blobId"), Column("Type", "type"), Column("Size", "size")]
    context.renderer.render(View([wire_of(uploaded)], columns, single=True))


@app.command()
def download(
    ctx: typer.Context,
    blob: Annotated[str, typer.Argument(help="The blob id.")],
    save: Annotated[
        str | None, typer.Option(help="Save to this file, or - for stdout (the default off a TTY).")
    ] = None,
    name: Annotated[str, typer.Option(help="The file name the server is told.")] = "download",
    content_type: Annotated[
        str, typer.Option("--type", help="The media type the server is told.")
    ] = "application/octet-stream",
    force: Annotated[bool, typer.Option("--force", help="Replace a file that exists.")] = False,
) -> None:
    """Download a blob's bytes."""
    context = cli(ctx)
    if save is None and context.out.is_terminal:
        raise UsageError("refusing to write binary data to a terminal", hint="pass --save FILE")
    data = context.client().download(blob, name=name, content_type=content_type)
    if save is None or save == "-":
        context.write_bytes(data)
        return
    write_file(Path(save), data, force=force)
    context.renderer.note(f"saved {save}")


@app.command()
def info(
    ctx: typer.Context,
    blobs: Blobs,
    digest: Annotated[
        list[str] | None, typer.Option(help="A digest to compute, such as sha-256 (repeatable).")
    ] = None,
) -> None:
    """Show each blob's size and, with --digest, its digests."""
    context = cli(ctx)
    algorithms = [name.lower() for name in digest or []]
    client = context.require("Blob/get")
    properties = ["size", *(f"digest:{name}" for name in algorithms)]
    with client.batch() as batch:
        handle = batch.blob.blob.get(ids=blobs, properties=properties)
    response = wire_of(handle.result)
    columns = [
        Column("BLOB", "id"),
        Column("SIZE", "size", lambda i: fmt.size(i.get("size")), justify="right"),
        *(Column(name.upper(), f"digest:{name}") for name in algorithms),
    ]
    context.renderer.render(View(response["list"], columns))
    _not_found(response)


@app.command()
def lookup(
    ctx: typer.Context,
    blobs: Blobs,
    type_names: Annotated[
        list[str] | None,
        typer.Option("--type", help="Data type to search, such as Email (repeatable)."),
    ] = None,
) -> None:
    """Show which objects refer to each blob."""
    context = cli(ctx)
    client = context.require("Blob/lookup")
    names = comma_list(type_names) or _lookup_types(client)
    with client.batch() as batch:
        handle = batch.blob.blob.lookup(type_names=names, ids=blobs)
    response = wire_of(handle.result)
    listed: list[dict[str, Any]] = response["list"]
    rows: list[dict[str, Any]] = []
    for item in listed:
        matched: dict[str, list[str]] = item.get("matchedIds") or {}
        rows += [{"id": item["id"], "type": name, "ids": ids} for name, ids in matched.items()]
    if context.options.json_mode:
        context.renderer.json(listed)
    else:
        columns = [
            Column("BLOB", "id"),
            Column("TYPE", "type"),
            Column("IDS", "ids", lambda i: ", ".join(i["ids"])),
        ]
        context.renderer.render(View(rows, columns))
    _not_found(response)


@app.command()
def copy(
    ctx: typer.Context,
    blobs: Blobs,
    source: Annotated[
        str, typer.Option("--from-account", help="The JMAP account the blobs are in now.")
    ],
) -> None:
    """Copy blobs from another JMAP account into this one."""
    context = cli(ctx)
    client = context.require("Blob/copy")
    with client.batch() as batch:
        handle = batch.core.blob.copy(from_account_id=source, blob_ids=blobs)
    response = wire_of(handle.result)
    copied: dict[str, str] = response.get("copied") or {}
    if context.options.json_mode:
        context.renderer.json(response)
    else:
        rows = [{"id": old, "copy": new} for old, new in copied.items()]
        context.renderer.render(View(rows, [Column("FROM", "id"), Column("TO", "copy")]))
    refused: dict[str, dict[str, Any]] = response.get("notCopied") or {}
    if refused:
        reasons = "; ".join(f"{key}: {error.get('type')}" for key, error in refused.items())
        raise PartialFailureError(f"{len(refused)} not copied: {reasons}")


def read_source(context: CLIContext, source: str) -> bytes:
    """The bytes of a file, or of stdin for ``-``."""
    if source == "-":
        buffer = getattr(context.stdin, "buffer", None)
        return buffer.read() if buffer is not None else context.stdin.read().encode("utf-8")
    try:
        return Path(source).expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {source}: {error.strerror or error}") from error


def _lookup_types(client: JMAPClient) -> list[str]:
    """The types the server can search that this build also knows."""
    account = client.session.capability_account(BLOB_URN, client.default_account)
    offered = BlobCapability.of(client.session.capability_value(BLOB_URN, account))
    names = [
        name
        for name in offered.supported_type_names
        if client.capabilities.urn_owning(name) is not None
    ]
    if not names:
        raise UnsupportedError("this server looks up no data types for blobs")
    return names


def _not_found(response: dict[str, Any]) -> None:
    missing: list[str] = response.get("notFound") or []
    if missing:
        raise NotFoundError(f"no blob {', '.join(missing)}")
