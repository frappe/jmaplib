"""``jmap compose``, ``send``, ``reply`` and ``forward``.

Every send is one request: ``Email/set`` creates the message in Drafts and
``EmailSubmission/set`` sends it, with ``onSuccessUpdateEmail`` moving it to
Sent and clearing ``$draft`` only if the send succeeds. A reply's original gets
``$answered`` in the same ``Email/set``. If the submission fails, the message
stays in Drafts, and the command says how to send it later.
"""

from __future__ import annotations

from typing import Annotated

import typer

from jmap.cli.commands import cli
from jmap.cli.commands.mail import select
from jmap.cli.commands.sending import Message, compose_draft, deliver, prelude, send_eml
from jmap.cli.errors import NotFoundError
from jmap.mail.render import body_text
from jmap.mail.reply import forward_draft, pick_identity, reply_draft

To = Annotated[list[str] | None, typer.Option("--to", help="Recipient (repeatable, or a, b).")]
Cc = Annotated[list[str] | None, typer.Option("--cc", help="Copy to (repeatable).")]
Bcc = Annotated[list[str] | None, typer.Option("--bcc", help="Blind copy to (repeatable).")]
Subject = Annotated[str | None, typer.Option(help="The subject.")]
Body = Annotated[str | None, typer.Option(help="The body text.")]
BodyFile = Annotated[
    str | None,
    typer.Option(help="Read the body from a file, or - for stdin; it may start with headers."),
]
Attach = Annotated[list[str] | None, typer.Option("--attach", help="File to attach (repeatable).")]
Sender = Annotated[str | None, typer.Option("--from", help="Identity to send as, by address.")]
Markdown = Annotated[bool, typer.Option("--markdown", help="The body is Markdown: send HTML.")]
Html = Annotated[bool, typer.Option("--html", help="The body is HTML.")]
SendAt = Annotated[
    str | None, typer.Option(help="Hold until a time (2026-11-01T09:00) or for +2h, +1d.")
]
Keyword = Annotated[list[str] | None, typer.Option("--keyword", help="Keyword to set.")]
SaveDraft = Annotated[bool, typer.Option("--draft", help="Save it in Drafts, do not send.")]
Edit = Annotated[
    bool | None,
    typer.Option("--edit/--no-edit", help="Open the editor (the default on a terminal)."),
]


def compose(
    ctx: typer.Context,
    to: To = None,
    cc: Cc = None,
    bcc: Bcc = None,
    subject: Subject = None,
    body: Body = None,
    body_file: BodyFile = None,
    attach: Attach = None,
    sender: Sender = None,
    markdown: Markdown = False,
    html: Html = False,
    send_at: SendAt = None,
    keyword: Keyword = None,
    draft: SaveDraft = False,
    edit: Edit = None,
) -> None:
    """Write a message in your editor, then send it (or save it with --draft)."""
    context = cli(ctx)
    message = Message(
        to, cc, bcc, subject, body, body_file, attach, markdown, html, send_at, keyword
    )
    found = prelude(context)
    written = compose_draft(context, message, found, sender=sender, edit=edit)
    deliver(context, written, found, save_only=draft)


def send(
    ctx: typer.Context,
    to: To = None,
    cc: Cc = None,
    bcc: Bcc = None,
    subject: Subject = None,
    body: Body = None,
    body_file: BodyFile = None,
    attach: Attach = None,
    sender: Sender = None,
    markdown: Markdown = False,
    html: Html = False,
    send_at: SendAt = None,
    keyword: Keyword = None,
    eml: Annotated[
        str | None, typer.Option(help="Send a whole message file (.eml), or - for stdin.")
    ] = None,
) -> None:
    """Send a message from the command line, without an editor."""
    context = cli(ctx)
    if eml is not None:
        send_eml(context, eml, sender=sender, send_at=send_at)
        return
    message = Message(
        to, cc, bcc, subject, body, body_file, attach, markdown, html, send_at, keyword
    )
    found = prelude(context)
    written = compose_draft(context, message, found, sender=sender, edit=False)
    deliver(context, written, found, save_only=False)


def reply(
    ctx: typer.Context,
    message: Annotated[str, typer.Argument(help="The message: a listing number or an id.")],
    reply_all: Annotated[bool, typer.Option("--all", help="Reply to every recipient.")] = False,
    no_quote: Annotated[
        bool, typer.Option("--no-quote", help="Do not quote the original.")
    ] = False,
    cc: Cc = None,
    body: Body = None,
    body_file: BodyFile = None,
    attach: Attach = None,
    sender: Sender = None,
    markdown: Markdown = False,
    send_at: SendAt = None,
    draft: SaveDraft = False,
    edit: Edit = None,
) -> None:
    """Reply to a message, quoting it, and mark it answered when sent."""
    context = cli(ctx)
    [email_id] = select(context, [message]).ids[:1]
    found = prelude(context, original=email_id)
    original = found.original
    if original is None:
        raise NotFoundError(f"no message {message!r}")
    identity = pick_identity(
        found.identities,
        wanted=sender,
        original=original,
        username=context.client().session.username,
    )
    base = reply_draft(
        original,
        identity=identity,
        reply_all=reply_all,
        quote_text=None if no_quote else body_text(original).text,
        me=[i.email for i in found.identities if i.email],
    )
    extra = Message(None, cc, None, None, body, body_file, attach, markdown, False, send_at, None)
    written = compose_draft(context, extra, found, sender=sender, edit=edit, base=base)
    deliver(context, written, found, save_only=draft, original=email_id, flag="$answered")


def forward(
    ctx: typer.Context,
    message: Annotated[str, typer.Argument(help="The message: a listing number or an id.")],
    to: To = None,
    cc: Cc = None,
    bcc: Bcc = None,
    as_attachment: Annotated[
        bool, typer.Option("--attach-original", help="Attach it whole, as a .eml file.")
    ] = False,
    body: Body = None,
    body_file: BodyFile = None,
    attach: Attach = None,
    sender: Sender = None,
    markdown: Markdown = False,
    send_at: SendAt = None,
    draft: SaveDraft = False,
    edit: Edit = None,
) -> None:
    """Forward a message with its attachments, and mark it forwarded when sent."""
    context = cli(ctx)
    [email_id] = select(context, [message]).ids[:1]
    found = prelude(context, original=email_id)
    original = found.original
    if original is None:
        raise NotFoundError(f"no message {message!r}")
    identity = pick_identity(
        found.identities,
        wanted=sender,
        original=original,
        username=context.client().session.username,
    )
    base = forward_draft(
        original,
        identity=identity,
        quote_text=body_text(original).text,
        as_attachment=as_attachment,
    )
    extra = Message(to, cc, bcc, None, body, body_file, attach, markdown, False, send_at, None)
    written = compose_draft(context, extra, found, sender=sender, edit=edit, base=base)
    deliver(context, written, found, save_only=draft, original=email_id, flag="$forwarded")
