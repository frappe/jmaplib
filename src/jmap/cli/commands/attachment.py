"""``jmap attachment``: a message's files, listed and saved."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from jmap.cli import format as fmt
from jmap.cli.commands import cli
from jmap.cli.commands.mail import select
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.files import safe_name, target_path, write_file
from jmap.cli.output import Column, View, wire_of
from jmap.mail.ops import queue_read
from jmap.mail.render import attachments_of
from jmap.models.mail.objects import EmailBodyPart

app: typer.Typer = typer.Typer(no_args_is_help=True)

Message = Annotated[str, typer.Argument(help="The message: a listing number or an id.")]


@app.command("list")
def list_(ctx: typer.Context, message: Message) -> None:
    """List a message's attachments, numbered for 'attachment download'."""
    context = cli(ctx)
    parts = _parts(context, message)
    items = [wire_of(part) for part in parts]
    context.renderer.render(
        View(
            items,
            [
                Column("NAME", "name"),
                Column("TYPE", "type"),
                Column("SIZE", "size", lambda i: fmt.size(i.get("size")), justify="right"),
            ],
            first_number=1,
        )
    )


@app.command()
def download(
    ctx: typer.Context,
    message: Message,
    which: Annotated[
        list[str] | None, typer.Argument(help="Attachment numbers or names; all if --all.")
    ] = None,
    every: Annotated[bool, typer.Option("--all", help="Every attachment.")] = False,
    directory: Annotated[Path, typer.Option("--dir", help="Where to save them.")] = Path(),
    save: Annotated[
        str | None, typer.Option(help="Save one attachment to this file, or - for stdout.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace files that exist.")] = False,
) -> None:
    """Save attachments; a name already taken gets a number, unless --force."""
    context = cli(ctx)
    parts = _chosen(_parts(context, message), which or [], every=every)
    if save is not None and len(parts) != 1:
        raise UsageError("--save takes one attachment", hint="use --dir for several")
    client = context.client()
    for number, part in enumerate(parts, start=1):
        name = safe_name(part.name, f"attachment-{number}")
        data = client.download(
            str(part.blob_id), name=name, content_type=part.type or "application/octet-stream"
        )
        if save == "-":
            context.write_bytes(data)
            continue
        path = Path(save) if save else target_path(directory, name, force=force)
        write_file(path, data, force=force or save is None)
        context.renderer.note(f"saved {path}")


def _parts(context: CLIContext, message: str) -> list[EmailBodyPart]:
    [email_id] = select(context, [message]).ids[:1]
    client = context.require("Email/get")
    with client.batch() as batch:
        read = queue_read(
            batch,
            [email_id],
            mark_seen=False,
            properties=["id", "attachments", "bodyStructure", "textBody", "htmlBody"],
        )
    [email] = read.emails.result.items or [None]
    if email is None:
        raise NotFoundError(f"no message {message!r}")
    return [part for part in attachments_of(email) if part.blob_id]


def _chosen(parts: list[EmailBodyPart], which: list[str], *, every: bool) -> list[EmailBodyPart]:
    if not parts:
        raise NotFoundError("this message has no attachments")
    if every or (not which and len(parts) == 1):
        return parts
    if not which:
        names = ", ".join(f"{n}. {p.name or '(unnamed)'}" for n, p in enumerate(parts, start=1))
        raise UsageError(f"which attachment? {names}", hint="name them, or pass --all")
    chosen: list[EmailBodyPart] = []
    for word in which:
        if word.isdigit() and 1 <= int(word) <= len(parts):
            chosen.append(parts[int(word) - 1])
            continue
        named = [part for part in parts if (part.name or "").lower() == word.lower()]
        if not named:
            raise NotFoundError(f"no attachment {word!r}", hint="see 'jmap attachment list'")
        chosen += named
    return chosen
