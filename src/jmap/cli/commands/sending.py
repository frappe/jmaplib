"""What every command that writes mail shares: options to a draft, the editor, delivery."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jmap.capabilities.mail import SUBMISSION_URN, SubmissionCapability, check_delayed_send
from jmap.cli.commands import report_set
from jmap.cli.context import CLIContext
from jmap.cli.editor import edit_text, editor_command
from jmap.cli.errors import (
    CLIError,
    NotFoundError,
    PartialFailureError,
    UnsupportedError,
    UsageError,
)
from jmap.cli.ids import Mailbox, MailboxIndex
from jmap.cli.output import wire_of
from jmap.core.patch import keyword_patch, parse_keyword
from jmap.mail.addresses import format_addresses, parse_addresses
from jmap.mail.compose import (
    AttachmentSpec,
    ContentType,
    Draft,
    DraftParseError,
    Uploaded,
    email_create,
    envelope,
    hold,
    parse_draft,
    render_draft,
    sent_patch,
)
from jmap.mail.ops import queue_import_and_send, queue_read, queue_send
from jmap.mail.reply import pick_identity
from jmap.models.mail.objects import Email, EmailAddress, Identity

#: A body that begins like this is a whole draft, headers and all.
_HEADER_LINE = re.compile(r"(From|To|Cc|Bcc|Subject|Reply-To|Attach|Send-At|Content-Type):", re.I)


@dataclass(frozen=True, slots=True)
class Message:
    """The options a person can give for a message."""

    to: list[str] | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    body_file: str | None = None
    attach: list[str] | None = None
    markdown: bool = False
    html: bool = False
    send_at: str | None = None
    keywords: list[str] | None = None

    @property
    def given_body(self) -> bool:
        return self.body is not None or self.body_file is not None


@dataclass(slots=True)
class Prelude:
    """What a send needs to know first, fetched in one request."""

    mailboxes: MailboxIndex
    identities: list[Identity] = field(default_factory=lambda: [])
    original: Email | None = None


def prelude(context: CLIContext, *, original: str | None = None) -> Prelude:
    """Mailboxes, identities and (for a reply or forward) the original, in one request."""
    client = context.require("Email/set")
    can_submit = client.capabilities.supports("Identity/get")
    with client.batch() as batch:
        boxes = MailboxIndex.queue(batch) if context.loaded_mailboxes is None else None
        identities = batch.add("Identity/get", {"ids": None}) if can_submit else None
        read = queue_read(batch, [original], mark_seen=False) if original else None
    if boxes is not None:
        context.adopt_mailboxes(MailboxIndex.from_handle(boxes))
    found = Prelude(context.mailboxes())
    if identities is not None:
        found.identities = list(identities.result.items)
    if read is not None:
        found.original = next(iter(read.emails.result.items), None)
    return found


def compose_draft(
    context: CLIContext,
    message: Message,
    found: Prelude,
    *,
    sender: str | None,
    edit: bool | None,
    base: Draft | None = None,
) -> Draft:
    """The draft the options describe, edited when wanted, From filled in."""
    draft = _apply(context, base or Draft(), message, prepend=base is not None)
    if edit is None:
        edit = context.stdin.isatty() and not message.given_body
    if not draft.from_:
        draft.from_ = _sender(context, found, sender)
    if edit:
        draft = edit_draft(context, draft, found.identities)
    if not draft.from_:  # deleted in the editor: send as the default identity
        draft.from_ = _sender(context, found, sender)
    return draft


def edit_draft(context: CLIContext, draft: Draft, identities: list[Identity]) -> Draft:
    """Open ``draft`` in the editor until it parses; unchanged means give up."""
    names = [format_addresses(_address(i)) for i in identities if i.email]
    original = render_draft(draft, identities=names)
    text = original
    command = editor_command(context)
    while True:
        edited = edit_text(command, text, suffix=".eml")
        if edited == original:
            raise CLIError("the message was not changed, so it was not sent")
        try:
            return parse_draft(edited)
        except DraftParseError as error:
            again = context.prompter.interactive and context.prompter.confirm(
                f"{error}. Edit it again?", default=True
            )
            if not again:
                raise UsageError(str(error)) from error
            text = f"# Error: {error}\n{edited}"


def deliver(
    context: CLIContext,
    draft: Draft,
    found: Prelude,
    *,
    save_only: bool,
    original: str | None = None,
    flag: str | None = None,
) -> None:
    """Save ``draft`` in Drafts, and unless ``save_only`` send it, in one request."""
    if not draft.recipients and not save_only:
        raise UsageError("the message has no recipients", hint="give --to, or a To: line")
    if not draft.body.strip() and not draft.attachments:
        raise UsageError("the message is empty, so it was not sent")
    drafts = _role(found, "drafts", "this account has no Drafts mailbox")
    client = context.require("Email/set") if save_only else context.require("EmailSubmission/set")
    create = email_create(
        draft,
        mailbox_id=str(drafts["id"]),
        uploaded=upload(context, draft),
        html_renderer=_markdown,
    )
    if save_only:
        with client.batch() as batch:
            handle = batch.add("Email/set", {"create": {"draft": create}})
        report_set(context, handle.result, "saved {n} draft(s)")
        return
    identity = identity_for(found, draft.from_)
    sent = _optional_role(found, "sent") if context.account.send_move_to_sent else None
    update = {original: keyword_patch(add=[flag])} if original and flag else None
    with client.batch() as batch:
        handles = queue_send(
            batch,
            create,
            identity_id=str(identity.id),
            envelope=_envelope(context, identity, draft),
            on_success=sent_patch(
                drafts_id=str(drafts["id"]), sent_id=str(sent["id"]) if sent else None
            ),
            update=update,
        )
    _report(context, handles.email, handles.submission, draft, original, flag)


def send_eml(context: CLIContext, source: str, *, sender: str | None, send_at: str | None) -> None:
    """Send a message file as it is: upload, import into Drafts, submit."""
    data = _stdin_bytes(context) if source == "-" else _read(source)
    found = prelude(context)
    client = context.require("Email/import", "EmailSubmission/set")
    identity = identity_for(found, _sender(context, found, sender))
    drafts = _role(found, "drafts", "this account has no Drafts mailbox")
    sent = _optional_role(found, "sent") if context.account.send_move_to_sent else None
    blob = client.upload(data, content_type="message/rfc822")
    with client.batch() as batch:
        handles = queue_import_and_send(
            batch,
            str(blob.blob_id),
            mailbox_id=str(drafts["id"]),
            identity_id=str(identity.id),
            envelope=_envelope(context, identity, Draft(send_at=send_at or "")),
            on_success=sent_patch(
                drafts_id=str(drafts["id"]), sent_id=str(sent["id"]) if sent else None
            ),
        )
    _report(context, handles.email, handles.submission, None, None, None)


def _apply(context: CLIContext, draft: Draft, message: Message, *, prepend: bool) -> Draft:
    """``draft`` with the options applied; a reply's own text goes above the quote."""
    text = _body(context, message)
    if text is not None and _HEADER_LINE.match(text):
        parsed = parse_draft(text)
        draft = replace(draft, **{k: v for k, v in _fields(parsed).items() if v})
        text = None
    try:
        updates: dict[str, Any] = {
            "to": [*draft.to, *_addresses(message.to)],
            "cc": [*draft.cc, *_addresses(message.cc)],
            "bcc": [*draft.bcc, *_addresses(message.bcc)],
            "keywords": [*draft.keywords, *(parse_keyword(k) for k in message.keywords or [])],
            "attachments": [
                *draft.attachments,
                *(AttachmentSpec(path=p) for p in message.attach or []),
            ],
        }
    except ValueError as error:
        raise UsageError(str(error)) from error
    if message.subject is not None:
        updates["subject"] = message.subject
    if text is not None:
        updates["body"] = text + draft.body if prepend else text
    if message.send_at is not None:
        hold(message.send_at, now=datetime.now(UTC))
        updates["send_at"] = message.send_at
    kind: ContentType | None = (
        "text/markdown" if message.markdown else "text/html" if message.html else None
    )
    if kind is not None:
        updates["content_type"] = kind
    return replace(draft, **updates)


def _fields(draft: Draft) -> dict[str, Any]:
    return {name: getattr(draft, name) for name in Draft.__dataclass_fields__}


def _body(context: CLIContext, message: Message) -> str | None:
    if message.body is not None:
        return message.body
    if message.body_file is None:
        return None
    if message.body_file == "-":
        return context.stdin.read()
    return _read(message.body_file).decode("utf-8")


def _stdin_bytes(context: CLIContext) -> bytes:
    buffer = getattr(context.stdin, "buffer", None)
    return buffer.read() if buffer is not None else context.stdin.read().encode("utf-8")


def _read(path: str) -> bytes:
    try:
        return Path(path).expanduser().read_bytes()
    except OSError as error:
        raise UsageError(f"cannot read {path}: {error.strerror or error}") from error


def _addresses(values: list[str] | None) -> list[Any]:
    return parse_addresses(", ".join(values)) if values else []


def _sender(context: CLIContext, found: Prelude, wanted: str | None) -> list[Any]:
    identity = pick_identity(
        found.identities, wanted=wanted, username=context.client().session.username
    )
    if identity is None:
        if wanted:
            raise NotFoundError(f"no identity sends as {wanted!r}", hint="see 'jmap identity list'")
        return []
    return _address(identity)


def _address(identity: Identity) -> list[Any]:
    if identity.name:
        return [EmailAddress(name=identity.name, email=identity.email)]
    return [EmailAddress(email=identity.email)]


def identity_for(found: Prelude, sender: list[EmailAddress]) -> Identity:
    """The identity whose address is ``sender``'s, which a submission must name."""
    address = (sender[0].email or "").lower() if sender else ""
    for identity in found.identities:
        if (identity.email or "").lower() == address:
            return identity
    raise NotFoundError(
        f"no identity sends as {address or 'the From address'}", hint="see 'jmap identity list'"
    )


def _envelope(context: CLIContext, identity: Identity, draft: Draft) -> dict[str, Any] | None:
    if not draft.send_at:
        return None
    client = context.client()
    value = client.session.capability_value(SUBMISSION_URN, client.default_account)
    capability = SubmissionCapability.of(value)
    if not capability.supports("FUTURERELEASE"):
        raise UnsupportedError("this server cannot hold a message to send later")
    now = datetime.now(UTC)
    check_delayed_send(hold(draft.send_at, now=now)[2], capability)
    return envelope(identity.email or "", draft, now=now)


def upload(context: CLIContext, draft: Draft) -> dict[str, Uploaded]:
    """Upload each attachment given as a path, keyed as ``email_create`` wants."""
    uploaded: dict[str, Uploaded] = {}
    for spec in draft.attachments:
        if spec.path is None:
            continue
        kind = spec.type or mimetypes.guess_type(spec.path)[0] or "application/octet-stream"
        result = context.client().upload(_read(spec.path), content_type=kind)
        uploaded[spec.key] = Uploaded(str(result.blob_id), result.type or kind, result.size)
    return uploaded


def _report(
    context: CLIContext,
    email: Any,
    submission: Any,
    draft: Draft | None,
    original: str | None,
    flag: str | None,
) -> None:
    """Say what happened; a message that was made but not sent stays in Drafts."""
    made = wire_of(email.result)
    created = _first(made, "created")
    if created is None:
        raise PartialFailureError(f"the message was not created: {_reasons(made)}")
    try:
        sent = submitted(submission, str(created.get("id")))
    except PartialFailureError:
        _undo_flag(context, original, flag)
        raise
    if context.options.json_mode:
        context.renderer.json([made, sent])
    who = format_addresses(draft.recipients) if draft else "its recipients"
    held = (_first(sent, "created") or {}).get("sendAt")
    later = f"; it goes out at {held}" if draft is not None and draft.send_at and held else ""
    context.renderer.note(f"sent to {who}{later}")


def submitted(submission: Any, email_id: str) -> dict[str, Any]:
    """The ``EmailSubmission/set`` response, or the error saying the message did not go."""
    error = submission.error
    sent: dict[str, Any] = {} if error is not None else wire_of(submission.result)
    if error is not None or _first(sent, "created") is None:
        reason = error.type if error is not None else _reasons(sent)
        raise PartialFailureError(
            f"the message was not sent ({reason}); it is saved in Drafts",
            hint=f"send it with 'jmap draft send id:{email_id}'",
        )
    return sent


def report_submission(context: CLIContext, submission: Any, email_id: str) -> None:
    """Say a saved draft went, or why it did not."""
    sent = submitted(submission, email_id)
    if context.options.json_mode:
        context.renderer.json(sent)
    context.renderer.note(f"sent draft {email_id}")


def _first(response: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The one object a single create made (or refused)."""
    found: dict[str, Any] = response.get(key) or {}
    first: dict[str, Any] | None = next(iter(found.values()), None)
    return first


def _reasons(response: dict[str, Any]) -> str:
    refused: dict[str, Any] = response.get("notCreated") or {}
    return "; ".join(str(error.get("type")) for error in refused.values()) or "no reason given"


def _undo_flag(context: CLIContext, original: str | None, flag: str | None) -> None:
    if not original or not flag:
        return
    client = context.client()
    with client.batch() as batch:
        batch.add("Email/set", {"update": {original: keyword_patch(remove=[flag])}})


def _role(found: Prelude, role: str, message: str) -> Mailbox:
    box = _optional_role(found, role)
    if box is None:
        raise NotFoundError(message)
    return box


def _optional_role(found: Prelude, role: str) -> Mailbox | None:
    for box in found.mailboxes.ordered():
        if box.get("role") == role:
            return box
    return None


def _markdown(text: str) -> str:
    from markdown_it import MarkdownIt

    rendered: str = MarkdownIt("commonmark").render(text)
    return rendered
