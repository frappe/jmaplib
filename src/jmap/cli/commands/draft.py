"""``jmap draft``: messages saved to finish later."""

from __future__ import annotations

from typing import Annotated

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.compose import Attach, Bcc, Body, BodyFile, Cc, Edit, Sender, Subject, To
from jmap.cli.commands.mail import Filters, Scope, run_listing, select
from jmap.cli.commands.sending import (
    Message,
    Prelude,
    compose_draft,
    deliver,
    edit_draft,
    identity_for,
    prelude,
    report_submission,
    upload,
)
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError
from jmap.mail.compose import draft_from_email, email_create, sent_patch

app: typer.Typer = typer.Typer(no_args_is_help=True)

Messages = Annotated[list[str], typer.Argument(help="Numbers from the last listing, or ids.")]
One = Annotated[str, typer.Argument(help="The draft: a listing number or an id.")]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List the drafts, newest first, numbered."""
    run_listing(cli(ctx), None, Filters(), Scope("drafts"), "-date", 25, 1, False, False, False)


@app.command()
def new(
    ctx: typer.Context,
    to: To = None,
    cc: Cc = None,
    bcc: Bcc = None,
    subject: Subject = None,
    body: Body = None,
    body_file: BodyFile = None,
    attach: Attach = None,
    sender: Sender = None,
    edit: Edit = None,
) -> None:
    """Write a message and save it to Drafts without sending it."""
    context = cli(ctx)
    message = Message(to, cc, bcc, subject, body, body_file, attach)
    found = prelude(context)
    deliver(
        context,
        compose_draft(context, message, found, sender=sender, edit=edit),
        found,
        save_only=True,
    )


@app.command()
def edit(
    ctx: typer.Context,
    draft: One,
    send_now: Annotated[bool, typer.Option("--send", help="Send it once edited.")] = False,
) -> None:
    """Edit a draft in your editor; the old one is replaced."""
    context = cli(ctx)
    [email_id] = select(context, [draft]).ids[:1]
    found = prelude(context, original=email_id)
    if found.original is None:
        raise NotFoundError(f"no draft {draft!r}")
    written = edit_draft(context, draft_from_email(found.original), found.identities)
    drafts = next(box for box in found.mailboxes.ordered() if box.get("role") == "drafts")
    create = email_create(written, mailbox_id=str(drafts["id"]), uploaded=upload(context, written))
    client = context.require("Email/set")
    with client.batch() as batch:
        handle = batch.add("Email/set", {"create": {"draft": create}, "destroy": [email_id]})
    report_set(context, handle.result, "saved the draft")
    if send_now:
        created = handle.result.created["draft"]
        _send(context, str(created.id), found)


@app.command()
def send(ctx: typer.Context, draft: One) -> None:
    """Send a draft as it is."""
    context = cli(ctx)
    [email_id] = select(context, [draft]).ids[:1]
    found = prelude(context, original=email_id)
    if found.original is None:
        raise NotFoundError(f"no draft {draft!r}")
    _send(context, email_id, found)


@app.command()
def delete(ctx: typer.Context, drafts: Messages) -> None:
    """Delete drafts, after you confirm."""
    context = cli(ctx)
    ids = select(context, drafts).ids
    context.confirm(f"Delete {len(ids)} draft(s)?")
    client = context.require("Email/set")
    with client.batch() as batch:
        handle = batch.add("Email/set", {"destroy": ids})
    report_set(context, handle.result, "deleted {n} draft(s)")


def _send(context: CLIContext, email_id: str, found: Prelude) -> None:
    """Submit a saved draft, moving it to Sent once it goes."""
    client = context.require("EmailSubmission/set")
    email = found.original
    identity = identity_for(found, list(email.from_ or []) if email else [])
    roles = {box.get("role"): str(box["id"]) for box in found.mailboxes.ordered()}
    sent = roles.get("sent") if context.account.send_move_to_sent else None
    with client.batch() as batch:
        handle = batch.add(
            "EmailSubmission/set",
            {
                "create": {"send": {"identityId": identity.id, "emailId": email_id}},
                "onSuccessUpdateEmail": {
                    "#send": sent_patch(drafts_id=roles.get("drafts"), sent_id=sent)
                },
            },
        )
    report_submission(context, handle, email_id)
