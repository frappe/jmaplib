"""``jmap mdn``: read receipts, sent and read (RFC 9007)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap.cli.commands import cli
from jmap.cli.commands.mail_select import select
from jmap.cli.errors import NotFoundError, PartialFailureError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.mail.reply import pick_identity
from jmap.models.mdn import (
    ACTION_AUTOMATIC,
    ACTION_MANUAL,
    DISPOSITION_TYPES,
    SENDING_AUTOMATIC,
    SENDING_MANUAL,
    TYPE_DISPLAYED,
    already_sent,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext
    from jmap.core.invocation import Handle
    from jmap.models.mail.objects import Email, Identity

app: typer.Typer = typer.Typer(no_args_is_help=True)

_BLOB = "blob:"
_FETCHED = ["id", "keywords", "from", "subject", "to", "cc", "bcc"]


@app.command()
def send(
    ctx: typer.Context,
    messages: Annotated[list[str], typer.Argument(help="Listing numbers or ids.")],
    kind: Annotated[
        str, typer.Option("--type", help="displayed, deleted, dispatched or processed.")
    ] = TYPE_DISPLAYED,
    auto: Annotated[
        bool, typer.Option("--auto", help="Sent by a program, not by your choice.")
    ] = False,
    include_original: Annotated[
        bool, typer.Option("--include-original", help="Quote the message back to its sender.")
    ] = False,
    subject: Annotated[str | None, typer.Option(help="The receipt's subject.")] = None,
    body: Annotated[str | None, typer.Option(help="The receipt's text.")] = None,
    sender: Annotated[str | None, typer.Option("--from", help="The identity to send as.")] = None,
) -> None:
    """Send a read receipt for each message that has none yet; marks them $mdnsent."""
    context = cli(ctx)
    if kind not in DISPOSITION_TYPES:
        raise UsageError(f"no receipt type {kind!r}", hint=", ".join(sorted(DISPOSITION_TYPES)))
    ids = select(context, messages).ids
    pending, identities = _unsent(context, ids)
    if not pending:
        return
    identity = pick_identity(
        identities, wanted=sender, original=pending[0], username=context.client().session.username
    )
    if identity is None:
        raise NotFoundError(f"no identity sends as {sender or 'you'}", hint="jmap identity list")
    receipt = {
        "disposition": {
            "actionMode": ACTION_AUTOMATIC if auto else ACTION_MANUAL,
            "sendingMode": SENDING_AUTOMATIC if auto else SENDING_MANUAL,
            "type": kind,
        },
        "includeOriginalMessage": include_original,
        **{key: value for key, value in (("subject", subject), ("textBody", body)) if value},
    }
    receipts = {f"r{n}": {"forEmailId": e.id, **receipt} for n, e in enumerate(pending, start=1)}
    client = context.require("MDN/send")
    with client.batch() as batch:
        handle = batch.mdn.mdn.send(identity_id=identity.id, send=receipts)
    _report(context, wire_of(handle.result), len(receipts))


@app.command()
def parse(
    ctx: typer.Context,
    sources: Annotated[
        list[str], typer.Argument(help="Receipts: listing numbers, ids, or blob:ID.")
    ],
) -> None:
    """Show what received read receipts say."""
    context = cli(ctx)
    blobs = [source.removeprefix(_BLOB) for source in sources if source.startswith(_BLOB)]
    words = [source for source in sources if not source.startswith(_BLOB)]
    ids = select(context, words).ids if words else []
    client = context.require("MDN/parse", *(["Email/get"] if ids else []))
    with client.batch() as batch:
        handles: list[Handle[Any]] = []
        if ids:
            emails = batch.add("Email/get", {"ids": ids, "properties": ["blobId"]})
            handles.append(batch.mdn.mdn.parse(blob_ids=emails.ref_list("blobId")))
        if blobs:
            handles.append(batch.mdn.mdn.parse(blob_ids=blobs))
    parsed: list[dict[str, Any]] = []
    for handle in handles:
        parsed += _parsed(context, wire_of(handle.result))
    if not parsed:
        raise NotFoundError("no read receipt among them")
    columns = [
        Column("FOR", "forEmailId"),
        Column("TYPE", "disposition", lambda i: _disposition(i, "type")),
        Column("ACTION", "disposition", lambda i: _disposition(i, "actionMode")),
        Column("RECIPIENT", "finalRecipient"),
        Column("SUBJECT", "subject"),
    ]
    context.renderer.render(View(parsed, columns))


def _unsent(context: CLIContext, ids: list[str]) -> tuple[list[Email], list[Identity]]:
    """The messages with no receipt sent yet, and the identities to send one as."""
    client = context.require("MDN/send", "Email/get", "Identity/get")
    with client.batch() as batch:
        emails = batch.add("Email/get", {"ids": ids, "properties": _FETCHED})
        identities = batch.add("Identity/get", {"ids": None})
    if emails.result.not_found:
        raise NotFoundError(f"no message {', '.join(emails.result.not_found)}")
    pending: list[Email] = []
    for email in emails.result.items:
        if already_sent(email.keywords):
            context.renderer.note(f"a receipt for {email.id} was sent already")
        else:
            pending.append(email)
    return pending, list(identities.result.items)


def _report(context: CLIContext, response: dict[str, Any], wanted: int) -> None:
    if context.options.json_mode:
        context.renderer.json(response)
    sent = len(response.get("sent") or {})
    if sent:
        context.renderer.note(f"sent {sent} read receipt(s)")
    refused: dict[str, dict[str, Any]] = response.get("notSent") or {}
    if refused:
        reasons = "; ".join(f"{key}: {error.get('type')}" for key, error in refused.items())
        raise PartialFailureError(f"{len(refused)} of {wanted} not sent: {reasons}")


def _parsed(context: CLIContext, response: dict[str, Any]) -> list[dict[str, Any]]:
    missing: list[str] = response.get("notFound") or []
    unparsable: list[str] = response.get("notParsable") or []
    for blob in missing:
        context.renderer.note(f"{blob}: not found")
    for blob in unparsable:
        context.renderer.note(f"{blob}: not a read receipt")
    found: dict[str, dict[str, Any]] = response.get("parsed") or {}
    return list(found.values())


def _disposition(item: Mapping[str, Any], key: str) -> str:
    disposition: dict[str, Any] = item.get("disposition") or {}
    return str(disposition.get(key) or "")
