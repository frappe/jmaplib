"""Filing and flagging: ``move``, ``copy``, ``archive``, ``delete``, ``flag``, ``seen`` and kin.

A move is a patch, never a rewrite of ``mailboxIds``: it adds the target and
removes the mailbox the messages were listed from (or ``--from``), so a message
also filed elsewhere stays there. When the source is unknown - the messages
were named by id - the message is put in the target alone, and the command
says so.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.capabilities.mail import MAIL_URN
from jmap.cli.commands import cli, report_set
from jmap.cli.commands.mail import app
from jmap.cli.commands.mail_select import Selection, select
from jmap.cli.connect import find_account
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.ids import Mailbox, MailboxIndex
from jmap.core.ids import Id
from jmap.core.patch import InvalidKeywordError, keyword_patch, mailbox_patch, parse_keyword

Messages = Annotated[
    list[str], typer.Argument(help="Numbers from the last listing (3, 1-5), or ids.")
]
Source = Annotated[str | None, typer.Option("--from", help="The mailbox to take them out of.")]

#: Bare names people type for the standard keywords.
_STANDARD: frozenset[str] = frozenset(
    {"seen", "flagged", "answered", "forwarded", "draft", "junk", "notjunk", "phishing"}
)


@app.command()
def move(
    ctx: typer.Context,
    messages: Messages,
    mailbox: Annotated[str, typer.Argument(help="Where to move them.")],
    source: Source = None,
) -> None:
    """Move messages to a mailbox: jmap move 1-3 Archive/2025."""
    context = cli(ctx)
    target = context.mailbox(mailbox)
    _file(context, select(context, messages), target, source, verb="moved")


@app.command()
def copy(
    ctx: typer.Context,
    messages: Messages,
    mailbox: Annotated[str, typer.Argument(help="Where to add them; with --to-account, there.")],
    account: Annotated[
        str | None,
        typer.Option("--to-account", help="Another JMAP account of this session: id or name."),
    ] = None,
) -> None:
    """Add messages to another mailbox too, keeping them where they are."""
    context = cli(ctx)
    selection = select(context, messages)
    if account is not None:
        _copy_across(context, selection, mailbox, account)
        return
    target = context.mailbox(mailbox)
    patch = mailbox_patch(add=[Id(str(target["id"]))])
    _update(context, selection, patch, f"copied {{n}} message(s) to {target['name']}")


@app.command()
def archive(ctx: typer.Context, messages: Messages, source: Source = None) -> None:
    """Move messages to the archive mailbox, out of the inbox (or where they were listed)."""
    context = cli(ctx)
    target = _role(context, "archive", "jmap mail move N MAILBOX")
    selection = select(context, messages)
    if source is None and selection.mailbox_id is None:
        inbox = _optional_role(context, "inbox")
        selection = Selection(selection.ids, str(inbox["id"]) if inbox else None)
    _file(context, selection, target, source, verb="archived")


@app.command()
def delete(
    ctx: typer.Context,
    messages: Messages,
    permanent: Annotated[
        bool, typer.Option("--permanent", help="Destroy them now, not move them to Trash.")
    ] = False,
    source: Source = None,
) -> None:
    """Move messages to Trash, or with --permanent destroy them."""
    context = cli(ctx)
    selection = select(context, messages)
    if permanent:
        context.confirm(f"Permanently delete {len(selection.ids)} message(s)?")
        client = context.require("Email/set")
        with client.batch() as batch:
            handle = batch.add("Email/set", {"destroy": selection.ids})
        report_set(context, handle.result, "deleted {n} message(s) permanently")
        return
    trash = _role(context, "trash", "pass --permanent to destroy them instead")
    if selection.mailbox_id == trash["id"] and source is None:
        raise UsageError("these are already in Trash", hint="pass --permanent to destroy them")
    _file(context, selection, trash, source, verb="moved")


@app.command()
def flag(ctx: typer.Context, messages: Messages) -> None:
    """Flag messages."""
    _keywords(cli(ctx), messages, add=["$flagged"], done="flagged {n} message(s)")


@app.command()
def unflag(ctx: typer.Context, messages: Messages) -> None:
    """Remove the flag from messages."""
    _keywords(cli(ctx), messages, remove=["$flagged"], done="unflagged {n} message(s)")


@app.command()
def seen(ctx: typer.Context, messages: Messages) -> None:
    """Mark messages read."""
    _keywords(cli(ctx), messages, add=["$seen"], done="marked {n} message(s) read")


@app.command()
def unseen(ctx: typer.Context, messages: Messages) -> None:
    """Mark messages unread."""
    _keywords(cli(ctx), messages, remove=["$seen"], done="marked {n} message(s) unread")


@app.command()
def junk(ctx: typer.Context, messages: Messages) -> None:
    """Mark messages as junk, and move them to the junk mailbox if there is one."""
    context = cli(ctx)
    keywords = keyword_patch(add=["$junk"], remove=["$notjunk"])
    target = _optional_role(context, "junk")
    _file(context, select(context, messages), target, None, verb="marked as junk", extra=keywords)


@app.command()
def notjunk(ctx: typer.Context, messages: Messages) -> None:
    """Mark messages as not junk, and move them back to the inbox."""
    context = cli(ctx)
    keywords = keyword_patch(add=["$notjunk"], remove=["$junk"])
    target = _optional_role(context, "inbox")
    _file(
        context, select(context, messages), target, None, verb="marked as not junk", extra=keywords
    )


@app.command()
def keyword(
    ctx: typer.Context,
    messages: Messages,
    add: Annotated[list[str] | None, typer.Option(help="Keyword to set (repeatable).")] = None,
    remove: Annotated[list[str] | None, typer.Option(help="Keyword to clear (repeatable).")] = None,
) -> None:
    """Set or clear any keyword: flagged, seen, or your own tags."""
    if not add and not remove:
        raise UsageError("give --add KEYWORD or --remove KEYWORD")
    _keywords(
        cli(ctx),
        messages,
        add=[keyword_name(word) for word in add or []],
        remove=[keyword_name(word) for word in remove or []],
        done="changed the keywords of {n} message(s)",
    )


def keyword_name(word: str) -> str:
    """``flagged`` means ``$flagged``; anything else is a keyword as typed."""
    name = word.lower()
    try:
        return parse_keyword(f"${name}" if name in _STANDARD else name)
    except InvalidKeywordError as error:
        raise UsageError(str(error)) from error


def _keywords(
    context: CLIContext,
    messages: list[str],
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    done: str,
) -> None:
    patch = keyword_patch(add=add or [], remove=remove or [])
    _update(context, select(context, messages), patch, done)


def _file(
    context: CLIContext,
    selection: Selection,
    target: Mailbox | None,
    source: str | None,
    *,
    verb: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Put the selection in ``target``, out of where it came from, plus ``extra``."""
    patch = dict(extra or {})
    where = ""
    if target is not None:
        to = str(target["id"])
        origin = str(context.mailbox(source)["id"]) if source else selection.mailbox_id
        if origin is None:
            patch["mailboxIds"] = {to: True}
            context.renderer.note(
                "the source mailbox is unknown, so each message is now only in "
                f"{target['name']}; list a mailbox first, or pass --from"
            )
        elif origin != to:
            patch.update(mailbox_patch(add=[Id(to)], remove=[Id(origin)]))
        where = f" to {target['name']}"
    _update(context, selection, patch, f"{verb} {{n}} message(s){where}")


def _copy_across(context: CLIContext, selection: Selection, mailbox: str, account: str) -> None:
    """``Email/copy`` into another JMAP account: one request finds the mailbox, one copies."""
    client = context.require("Email/copy")
    target = find_account(client.session, account)
    source = client.default_account or client.session.primary_account_for(MAIL_URN)
    if target == source:
        raise UsageError("--to-account names this account", hint="leave it out to copy here")
    with client.batch() as batch:
        handle = MailboxIndex.queue(batch, account_id=target)
    there = MailboxIndex.from_handle(handle).resolve(mailbox)
    create = {
        f"c{number}": {"id": email_id, "mailboxIds": {str(there["id"]): True}}
        for number, email_id in enumerate(selection.ids)
    }
    with client.batch() as batch:
        copied = batch.add(
            "Email/copy", {"fromAccountId": source, "create": create}, account_id=target
        )
    report_set(context, copied.result, f"copied {{n}} message(s) to {there['name']} in {account}")


def _update(context: CLIContext, selection: Selection, patch: dict[str, Any], done: str) -> None:
    """Apply one patch to every selected message, in one ``Email/set``."""
    client = context.require("Email/set")
    with client.batch() as batch:
        handle = batch.add("Email/set", {"update": dict.fromkeys(selection.ids, patch)})
    report_set(context, handle.result, done)


def _role(context: CLIContext, role: str, hint: str) -> Mailbox:
    found = _optional_role(context, role)
    if found is None:
        raise NotFoundError(f"this account has no {role} mailbox", hint=hint)
    return found


def _optional_role(context: CLIContext, role: str) -> Mailbox | None:
    try:
        return context.mailbox(role)
    except NotFoundError:
        return None
