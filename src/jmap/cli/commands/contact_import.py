"""``jmap contact import``: vCards in, through the server's own parser.

The server turns vCard into JSContact with ``ContactCard/parse`` (Stalwart's
``urn:ietf:params:jmap:contacts:parse``), which reads one card per blob. So the
file is split into its cards, each is uploaded, all are parsed, and the parsed
cards are created. The server caps blobs per parse without saying where, so a
``requestTooLarge`` answer means: send half as many, and go on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, TypeVar

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.addressbook import book_for
from jmap.cli.commands.contact import BookOption, app
from jmap.cli.errors import PartialFailureError, UsageError
from jmap.cli.output import wire_of
from jmap.core.errors import MethodError

if TYPE_CHECKING:
    from collections.abc import Callable

    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient

T = TypeVar("T")
R = TypeVar("R")

#: One vCard, from its BEGIN line to its END line.
_VCARD: Final = re.compile(rb"^BEGIN:VCARD\s*?$.*?^END:VCARD\s*?$", re.IGNORECASE | re.M | re.S)


@app.command("import")
def import_(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="A vCard file (.vcf), or - for stdin.")],
    book: BookOption = None,
) -> None:
    """Add the cards in a vCard file to --book or the default address book."""
    context = cli(ctx)
    cards = split_vcards(_read(context, source))
    if not cards:
        raise UsageError(f"{source} holds no vCard")
    client = context.require("ContactCard/parse", "ContactCard/set")
    chosen = book_for(context, book)
    blobs = [str(client.upload(card, content_type="text/vcard").blob_id) for card in cards]
    parsed: dict[str, Any] = {}
    for result in in_halves(blobs, len(blobs), lambda chunk: _parse(client, chunk)):
        parsed.update(result.get("parsed") or {})
    creates: dict[str, Any] = {}
    unreadable: list[str] = []
    for number, blob in enumerate(blobs, 1):
        if blob in parsed:
            card = {key: value for key, value in parsed[blob].items() if key != "id"}
            creates[f"card{number}"] = {**card, "addressBookIds": {chosen["id"]: True}}
        else:
            unreadable.append(f"card{number}")
    for name in unreadable:
        context.renderer.note(f"{name}: the server could not read it")
    if creates:
        size = client.capabilities.limits.max_objects_in_set
        responses = in_halves(list(creates.items()), size, lambda chunk: _create(client, chunk))
        report_set(context, _merged(responses), f"imported {{n}} contact(s) into {chosen['name']}")
    if unreadable:
        raise PartialFailureError(f"{len(unreadable)} card(s) not read: {', '.join(unreadable)}")


def split_vcards(data: bytes) -> list[bytes]:
    """Each vCard in ``data``, on its own, ending in CRLF."""
    return [found.group(0).rstrip() + b"\r\n" for found in _VCARD.finditer(data)]


def in_halves(items: list[T], size: int, send: Callable[[list[T]], R]) -> list[R]:
    """``send`` ``items`` ``size`` at a time, halving the size on ``requestTooLarge``."""
    results: list[R] = []
    done = 0
    while done < len(items):
        chunk = items[done : done + size]
        try:
            results.append(send(chunk))
        except MethodError as error:
            if error.type != "requestTooLarge" or len(chunk) == 1:
                raise
            size = len(chunk) // 2
            continue
        done += len(chunk)
    return results


def _parse(client: JMAPClient, blobs: list[str]) -> dict[str, Any]:
    with client.batch() as batch:
        handle = batch.add("ContactCard/parse", {"blobIds": blobs})
    parsed: dict[str, Any] = wire_of(handle.result)
    return parsed


def _create(client: JMAPClient, chunk: list[tuple[str, Any]]) -> dict[str, Any]:
    with client.batch() as batch:
        handle = batch.add("ContactCard/set", {"create": dict(chunk)})
    response: dict[str, Any] = wire_of(handle.result)
    return response


def _merged(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Several ``ContactCard/set`` answers as one, in the same wire form."""
    merged: dict[str, Any] = {**responses[0], "created": {}, "notCreated": {}}
    for response in responses:
        merged["created"].update(response.get("created") or {})
        merged["notCreated"].update(response.get("notCreated") or {})
    merged["newState"] = responses[-1].get("newState")
    return merged


def _read(context: CLIContext, source: str) -> bytes:
    if source == "-":
        buffer = getattr(context.stdin, "buffer", None)
        return buffer.read() if buffer is not None else context.stdin.read().encode("utf-8")
    try:
        return Path(source).expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {source}: {error.strerror or error}") from error
