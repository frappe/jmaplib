"""Changing mailboxes: ``create``, ``rename``, ``move``, ``delete``, ``subscribe``, ``empty``."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.mail import combine, query_text
from jmap.cli.commands.mailbox import MailboxRef, app
from jmap.cli.context import CLIContext
from jmap.cli.errors import UsageError
from jmap.mail.query import Term, parse, to_filter

#: Ids fetched per ``Email/query`` while emptying a mailbox.
_EMPTY_PAGE: int = 512


@app.command()
def create(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Name, or a path whose parent exists: Archive/2026.")],
    role: Annotated[
        str | None, typer.Option(help="A role such as archive, if it has none yet.")
    ] = None,
) -> None:
    """Create a mailbox."""
    context = cli(ctx)
    parent_path, _, name = path.strip("/").rpartition("/")
    parent = context.mailbox(parent_path) if parent_path else None
    mailbox: dict[str, Any] = {"name": name, "parentId": parent["id"] if parent else None}
    if role:
        mailbox["role"] = role.lower()
    _set(context, {"create": {"new": mailbox}}, f"created {path}")


@app.command()
def rename(
    ctx: typer.Context,
    mailbox: MailboxRef,
    name: Annotated[str, typer.Argument(help="The new name (not a path).")],
) -> None:
    """Rename a mailbox, leaving it where it is."""
    if "/" in name:
        raise UsageError("give a name, not a path", hint="move it with 'jmap mailbox move'")
    context = cli(ctx)
    found = context.mailbox(mailbox)
    _set(context, {"update": {found["id"]: {"name": name}}}, f"renamed {found['name']} to {name}")


@app.command("move")
def move_(
    ctx: typer.Context,
    mailbox: MailboxRef,
    parent: Annotated[
        str | None, typer.Argument(help="The new parent; leave out for the top level.")
    ] = None,
) -> None:
    """Move a mailbox under another one, or to the top level."""
    context = cli(ctx)
    index = context.mailboxes()
    found = context.mailbox(mailbox)
    under = context.mailbox(parent)["id"] if parent else None
    where = index.path_of(str(under)) if under else "the top level"
    _set(
        context, {"update": {found["id"]: {"parentId": under}}}, f"moved {found['name']} to {where}"
    )


@app.command()
def delete(
    ctx: typer.Context,
    mailbox: MailboxRef,
    with_mail: Annotated[
        bool, typer.Option("--with-mail", help="Also destroy mail that is only in it.")
    ] = False,
) -> None:
    """Delete a mailbox. The server refuses one that still holds mail, unless --with-mail."""
    context = cli(ctx)
    found = context.mailbox(mailbox)
    path = context.mailboxes().path_of(str(found["id"]))
    extra = " and the mail only in it" if with_mail else ""
    context.confirm(f"Delete the mailbox {path}{extra}?")
    arguments = {"destroy": [found["id"]], "onDestroyRemoveEmails": with_mail}
    _set(context, arguments, f"deleted {path}")


@app.command()
def subscribe(ctx: typer.Context, mailbox: MailboxRef) -> None:
    """Show a mailbox in mail clients' folder lists."""
    _subscribed(cli(ctx), mailbox, value=True)


@app.command()
def unsubscribe(ctx: typer.Context, mailbox: MailboxRef) -> None:
    """Hide a mailbox from mail clients' folder lists."""
    _subscribed(cli(ctx), mailbox, value=False)


@app.command()
def empty(
    ctx: typer.Context,
    mailbox: MailboxRef,
    match: Annotated[
        str | None, typer.Option(help="Only mail matching this search, such as before:30d.")
    ] = None,
) -> None:
    """Permanently destroy the mail in a mailbox, such as Trash or Junk."""
    context = cli(ctx)
    found = context.mailbox(mailbox)
    node = combine(parse(query_text([match] if match else [])), [Term("in", f"id:{found['id']}")])
    assert node is not None  # it holds at least the in: term
    query = to_filter(node, mailbox_id=lambda name: str(context.mailbox(name)["id"]))
    ids = _all_ids(context, query)
    path = context.mailboxes().path_of(str(found["id"]))
    if not ids:
        context.renderer.note(f"{path} has nothing to delete")
        return
    context.confirm(f"Permanently delete {len(ids)} message(s) in {path}?")
    client = context.require("Email/set")
    with client.batch() as batch:
        handle = batch.add("Email/set", {"destroy": ids})
    context.forget_mailboxes()
    report_set(context, handle.result, f"deleted {{n}} message(s) in {path}")


def _all_ids(context: CLIContext, query: dict[str, Any]) -> list[str]:
    client = context.require("Email/query")
    ids: list[str] = []
    while True:
        with client.batch() as batch:
            handle = batch.add(
                "Email/query", {"filter": query, "position": len(ids), "limit": _EMPTY_PAGE}
            )
        page = [str(email_id) for email_id in handle.result.ids]
        ids += page
        if len(page) < _EMPTY_PAGE:
            return ids


def _subscribed(context: CLIContext, mailbox: str, *, value: bool) -> None:
    found = context.mailbox(mailbox)
    verb = "subscribed to" if value else "unsubscribed from"
    _set(context, {"update": {found["id"]: {"isSubscribed": value}}}, f"{verb} {found['name']}")


def _set(context: CLIContext, arguments: dict[str, Any], done: str) -> None:
    client = context.require("Mailbox/set")
    with client.batch() as batch:
        handle = batch.add("Mailbox/set", arguments)
    context.forget_mailboxes()
    report_set(context, handle.result, done)
