"""``jmap mail import`` and ``export``: messages in and out as ``.eml`` files or an mbox."""

from __future__ import annotations

import mailbox
from pathlib import Path
from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.mail import app
from jmap.cli.commands.mail_select import select
from jmap.cli.commands.mail_triage import keyword_name
from jmap.cli.context import CLIContext
from jmap.cli.errors import PartialFailureError, UsageError
from jmap.cli.files import safe_name, target_path, write_file
from jmap.cli.output import wire_of


@app.command("import")
def import_(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="A message file (.eml), or - for stdin.")],
    mailbox_ref: Annotated[str, typer.Option("--to", help="The mailbox to put it in.")] = "inbox",
    keyword: Annotated[list[str] | None, typer.Option(help="Keyword to set (repeatable).")] = None,
    seen: Annotated[bool, typer.Option("--seen", help="Mark it read.")] = False,
    received_at: Annotated[
        str | None, typer.Option(help="When it arrived: 2026-10-30T09:00:00Z.")
    ] = None,
    strict: Annotated[
        bool, typer.Option("--strict", help="Fail if the message is already there.")
    ] = False,
) -> None:
    """Add a message file to a mailbox, as it is, without sending it."""
    context = cli(ctx)
    data = _read(context, source)
    target = context.mailbox(mailbox_ref)
    keywords = [keyword_name(word) for word in keyword or []] + (["$seen"] if seen else [])
    client = context.require("Email/import")
    blob = client.upload(data, content_type="message/rfc822")
    imported: dict[str, Any] = {
        "blobId": blob.blob_id,
        "mailboxIds": {target["id"]: True},
        "keywords": dict.fromkeys(keywords, True),
    }
    if received_at is not None:
        imported["receivedAt"] = received_at
    with client.batch() as batch:
        handle = batch.add("Email/import", {"emails": {"message": imported}})
    result = wire_of(handle.result)
    refusals: dict[str, dict[str, Any]] = result.get("notCreated") or {}
    refused = refusals.get("message")
    if refused and refused.get("type") == "alreadyExists" and not strict:
        context.renderer.note(f"already there, as {refused.get('existingId')}")
        return
    report_set(context, handle.result, f"imported {{n}} message(s) into {target['name']}")


@app.command()
def export(
    ctx: typer.Context,
    messages: Annotated[list[str], typer.Argument(help="Numbers from the last listing, or ids.")],
    directory: Annotated[
        Path, typer.Option("--dir", help="Save each as an .eml file here.")
    ] = Path(),
    save: Annotated[
        str | None, typer.Option(help="Save one message to this file, or - for stdout.")
    ] = None,
    mbox: Annotated[Path | None, typer.Option(help="Append them all to this mbox file.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace files that exist.")] = False,
) -> None:
    """Save messages as they were sent: .eml files, or one mbox."""
    context = cli(ctx)
    ids = select(context, messages).ids
    if save is not None and len(ids) != 1:
        raise UsageError("--save takes one message", hint="use --dir or --mbox for several")
    client = context.require("Email/get")
    with client.batch() as batch:
        handle = batch.add("Email/get", {"ids": ids, "properties": ["blobId", "subject"]})
    found: list[dict[str, Any]] = wire_of(handle.result)["list"]
    missing = set(ids) - {item["id"] for item in found}
    store = mailbox.mbox(str(mbox)) if mbox is not None else None
    for item in found:
        data = client.download(
            str(item["blobId"]), name="message.eml", content_type="message/rfc822"
        )
        if store is not None:
            store.add(mailbox.mboxMessage(data))
        elif save == "-":
            context.write_bytes(data)
        else:
            name = safe_name(f"{item.get('subject') or item['id']}.eml", f"{item['id']}.eml")
            path = Path(save) if save else target_path(directory, name, force=force)
            write_file(path, data, force=force or save is None)
            context.renderer.note(f"saved {path}")
    if store is not None:
        store.close()
        context.renderer.note(f"added {len(found)} message(s) to {mbox}")
    if missing:
        raise PartialFailureError(f"not found: {', '.join(sorted(missing))}")


def _read(context: CLIContext, source: str) -> bytes:
    if source == "-":
        buffer = getattr(context.stdin, "buffer", None)
        return buffer.read() if buffer is not None else context.stdin.read().encode("utf-8")
    try:
        return Path(source).expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {source}: {error.strerror or error}") from error
