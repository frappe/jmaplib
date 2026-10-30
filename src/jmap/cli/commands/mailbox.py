"""``jmap mailbox``: list and inspect mailboxes."""

from __future__ import annotations

from typing import Annotated, Any, cast

import typer

from jmap.cli.commands import cli
from jmap.cli.completers import mailboxes
from jmap.cli.ids import Mailbox, MailboxIndex
from jmap.cli.output import Column, View, wire_of

app: typer.Typer = typer.Typer(no_args_is_help=True)

MailboxRef = Annotated[
    str,
    typer.Argument(
        help="Role (inbox, sent...), path (Archive/2025), name, or id.", autocompletion=mailboxes
    ),
]


@app.command("list")
def list_(
    ctx: typer.Context,
    tree: Annotated[bool, typer.Option("--tree", help="Indent names instead of paths.")] = False,
    subscribed: Annotated[
        bool, typer.Option("--subscribed", help="Only subscribed mailboxes.")
    ] = False,
) -> None:
    """List every mailbox, parents before children."""
    context = cli(ctx)
    index = context.mailboxes()
    boxes = [box for box in index.ordered() if box.get("isSubscribed") or not subscribed]
    context.renderer.render(
        View(
            items=boxes,
            columns=[
                Column("NAME", "name", lambda box: _label(index, box, tree=tree)),
                Column("ROLE", "role"),
                Column("UNREAD", "unreadEmails", justify="right"),
                Column("TOTAL", "totalEmails", justify="right"),
                Column("ID", "id"),
            ],
        )
    )


@app.command()
def show(ctx: typer.Context, mailbox: MailboxRef) -> None:
    """Show one mailbox, with its counts and your rights on it."""
    context = cli(ctx)
    index = context.mailboxes()
    found = context.mailbox(mailbox)
    client = context.client()
    with client.batch() as batch:
        handle = batch.add("Mailbox/get", {"ids": [found["id"]]})
    box: Mailbox = (wire_of(handle.result)["list"] or [found])[0]
    context.renderer.render(
        View(
            items=[box],
            columns=[
                Column("ID", "id"),
                Column("Name", "name"),
                Column("Path", "path", lambda b: index.path_of(str(b["id"]))),
                Column("Role", "role"),
                Column("Parent", "parentId", lambda b: _parent(index, b)),
                Column("Unread", "unreadEmails"),
                Column("Total", "totalEmails"),
                Column("Unread threads", "unreadThreads"),
                Column("Threads", "totalThreads"),
                Column("Subscribed", "isSubscribed", lambda b: _yes(b.get("isSubscribed"))),
                Column("Rights", "myRights", lambda b: _rights(b.get("myRights"))),
            ],
            single=True,
        )
    )


def _label(index: MailboxIndex, box: Mailbox, *, tree: bool) -> str:
    if tree:
        return "  " * index.depth_of(str(box["id"])) + str(box.get("name") or "")
    return index.path_of(str(box["id"]))


def _parent(index: MailboxIndex, box: Mailbox) -> str:
    parent = box.get("parentId")
    return index.path_of(str(parent)) if parent else ""


def _rights(rights: Any) -> str:
    if not isinstance(rights, dict):
        return ""
    named = cast("dict[str, Any]", rights)
    granted = [name.removeprefix("may") for name, allowed in named.items() if allowed]
    return ", ".join(granted)


def _yes(value: object) -> str:
    return "yes" if value else "no"
