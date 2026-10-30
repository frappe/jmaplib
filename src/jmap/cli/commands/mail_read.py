"""``jmap mail read`` and ``thread``: showing messages."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from jmap.cli import format as fmt
from jmap.cli.commands import cli
from jmap.cli.commands.mail import app
from jmap.cli.commands.mail_select import select
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.files import write_file
from jmap.cli.ids import Listing
from jmap.cli.output import Column, OutputFormat, View, wire_of
from jmap.core.narrow import as_list
from jmap.mail.ops import DEFAULT_MAX_BODY_BYTES, LIST_PROPERTIES, queue_read, queue_thread
from jmap.mail.render import attachments_of, body_text
from jmap.models.mail.objects import Email, EmailBodyPart

Messages = Annotated[
    list[str], typer.Argument(help="Numbers from the last listing (3, 1-5), or ids.")
]


@app.command()
def read(
    ctx: typer.Context,
    messages: Messages,
    html: Annotated[bool, typer.Option("--html", help="Read the HTML part, as text.")] = False,
    raw: Annotated[bool, typer.Option("--raw", help="The message source, as sent.")] = False,
    part: Annotated[
        str | None, typer.Option(help="One body part's bytes, by its part id (2, 1.3).")
    ] = None,
    save: Annotated[
        Path | None, typer.Option(help="With --raw or --part: write to this file, not stdout.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace the --save file.")] = False,
    headers: Annotated[
        str | None, typer.Option(help="More headers to show, comma separated.")
    ] = None,
    all_headers: Annotated[bool, typer.Option("--all-headers", help="Every header.")] = False,
    mark_seen: Annotated[
        bool, typer.Option("--mark-seen/--no-mark-seen", help="Mark them read.")
    ] = True,
    max_body_bytes: Annotated[
        int, typer.Option(min=1, help="Fetch at most this much of each body.")
    ] = DEFAULT_MAX_BODY_BYTES,
) -> None:
    """Show messages: headers, the body as text, and the attachments."""
    context = cli(ctx)
    selection = select(context, messages)
    if raw or part is not None:
        data = _part(context, selection.ids, part) if part else _raw(context, selection.ids)
        if save is None:
            context.write_bytes(data)
        else:
            write_file(save, data, force=force)
            context.renderer.note(f"saved {save}")
        return
    client = context.require("Email/get")
    account = client.default_account
    writable = account is None or not client.session.is_read_only(account)
    names = [name.strip() for name in (headers or "").split(",") if name.strip()]
    with client.batch() as batch:
        handles = queue_read(
            batch,
            selection.ids,
            headers=names,
            all_headers=all_headers,
            mark_seen=mark_seen
            and writable
            and context.guard.writes_allowed
            and client.capabilities.supports("Email/set"),
            max_body_bytes=max_body_bytes,
        )
    emails: list[Email] = handles.emails.result.items
    if context.options.json_mode:
        items = [wire_of(email) for email in emails]
        context.renderer.render(View(items, [], single=len(selection.ids) == 1))
        return
    for position, email in enumerate(emails):
        if position:
            context.out.rule(style="dim")
        show(context, email, names, prefer="html" if html else "text", everything=all_headers)


def show(
    context: CLIContext,
    email: Email,
    headers: list[str],
    *,
    prefer: str = "text",
    everything: bool = False,
) -> None:
    """One message as a person reads it."""
    wire = wire_of(email)
    fields = [
        ("From", fmt.address_list(wire.get("from"))),
        ("To", fmt.address_list(wire.get("to"))),
        ("Cc", fmt.address_list(wire.get("cc"))),
        ("Date", _date(email.sent_at or email.received_at)),
        ("Subject", email.subject or ""),
        *((name, _header_text(email.header(f"header:{name}:asText"))) for name in headers),
        *(
            (header.name or "", header.value or "")
            for header in (email.headers or [])
            if everything
        ),
    ]
    out = context.out
    for label, value in fields:
        if value:
            out.print(f"[bold]{escape(label)}:[/] {escape(value.strip())}", soft_wrap=True)
    out.print()
    body = body_text(email, prefer="html" if prefer == "html" else "text")
    out.print(body.text, markup=False, highlight=False, soft_wrap=True)
    if body.truncated:
        context.renderer.note("this body was cut short: pass a larger --max-body-bytes")
    if body.encoding_problem:
        context.renderer.note("the server could not decode part of this body cleanly")
    _attachments(context, email)


@app.command()
def thread(
    ctx: typer.Context,
    message: Annotated[str, typer.Argument(help="Any message in the conversation.")],
    read_all: Annotated[bool, typer.Option("--read", help="Show every message in full.")] = False,
) -> None:
    """List a conversation, oldest first, numbered like a listing."""
    context = cli(ctx)
    [email_id, *_] = select(context, [message]).ids
    client = context.require("Email/get", "Thread/get")
    with client.batch() as batch:
        handles = queue_thread(batch, email_id)
    error = handles.emails.error
    if error is not None and error.type == "invalidResultReference":
        rows = _thread_in_two(context, handles.start.result.items[0].thread_id)
    else:
        rows = wire_of(handles.emails.result)["list"]
    if read_all and not context.options.json_mode:
        ids = [row["id"] for row in rows]
        with client.batch() as batch:
            full = queue_read(batch, ids, mark_seen=False)
        for position, email in enumerate(full.emails.result.items):
            if position:
                context.out.rule(style="dim")
            show(context, email, [])
    else:
        _list(context, rows)
    context.cursor.record(Listing(ids=tuple(str(row["id"]) for row in rows)))


def _thread_in_two(context: CLIContext, thread_id: str | None) -> list[dict[str, Any]]:
    """The thread without the flattened back-reference some servers refuse."""
    client = context.client()
    with client.batch() as batch:
        thread = batch.add("Thread/get", {"ids": [thread_id]})
    threads = wire_of(thread.result)["list"]
    email_ids = [str(i) for item in threads for i in as_list(item.get("emailIds") or [])]
    with client.batch() as batch:
        emails = batch.add("Email/get", {"ids": email_ids, "properties": list(LIST_PROPERTIES)})
    found = {row["id"]: row for row in wire_of(emails.result)["list"]}
    return [found[i] for i in email_ids if i in found]


def _list(context: CLIContext, rows: list[dict[str, Any]]) -> None:
    table = context.renderer.format is OutputFormat.TABLE
    now = datetime.now().astimezone()
    columns = [
        Column("FLAGS", "keywords", fmt.flags),
        Column(
            "DATE",
            "receivedAt",
            (lambda r: fmt.when(r.get("receivedAt"), now=now)) if table else None,
        ),
        Column("FROM", "from", lambda r: fmt.address(r.get("from"))),
        Column("SUBJECT", "subject"),
    ]
    context.renderer.render(View(rows, columns, first_number=1))


def _raw(context: CLIContext, ids: list[str]) -> bytes:
    if len(ids) != 1:
        raise UsageError("--raw shows one message at a time")
    client = context.require("Email/get")
    with client.batch() as batch:
        handle = batch.add("Email/get", {"ids": ids, "properties": ["blobId"]})
    [email] = handle.result.items or [None]
    if email is None or not email.blob_id:
        raise UsageError(f"the server returned no source for {ids[0]}")
    return client.download(email.blob_id, name="message.eml", content_type="message/rfc822")


def _part(context: CLIContext, ids: list[str], part_id: str) -> bytes:
    """The decoded bytes of one part, found in the message's ``bodyStructure``."""
    if len(ids) != 1:
        raise UsageError("--part reads one message at a time")
    client = context.require("Email/get")
    with client.batch() as batch:
        handle = batch.add("Email/get", {"ids": ids, "properties": ["bodyStructure"]})
    [email] = handle.result.items or [None]
    root = email.body_structure if email is not None else None
    parts = [part for part in _leaves(root) if part.part_id and part.blob_id]
    found = next((part for part in parts if part.part_id == part_id), None)
    if found is None:
        known = ", ".join(f"{part.part_id} ({part.type})" for part in parts)
        hint = f"its parts are: {known}" if known else None
        raise NotFoundError(f"message {ids[0]} has no part {part_id!r}", hint=hint)
    kind = found.type or "application/octet-stream"
    return client.download(str(found.blob_id), name=found.name or "part", content_type=kind)


def _leaves(part: EmailBodyPart | None) -> Iterator[EmailBodyPart]:
    """``part`` and every part under it, depth first."""
    if part is None:
        return
    yield part
    for child in part.sub_parts or []:
        yield from _leaves(child)


def _attachments(context: CLIContext, email: Email) -> None:
    files = attachments_of(email)
    if not files:
        return
    context.out.print()
    for number, part in enumerate(files, start=1):
        kind = part.type or "application/octet-stream"
        detail = f"{kind}, {fmt.size(part.size)}"
        context.out.print(
            f"[dim]{number}.[/] {escape(part.name or '(unnamed)')} [dim]({escape(detail)})[/]"
        )


def _date(value: str | None) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return value
    return moment.strftime("%a, %d %b %Y %H:%M %z")


def _header_text(value: Any) -> str:
    return "" if value is None else str(value)
