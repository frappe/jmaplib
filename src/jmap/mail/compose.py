"""A message being written: its text form for an editor, and the ``Email/set`` it becomes.

A :class:`Draft` is what a person composes. :func:`parse_draft` and
:func:`render_draft` turn it into and out of a small text format - headers, a
blank line, the body - that any editor can work on::

    From: Alice <alice@example.com>
    To: bob@example.com
    Subject: Lunch?
    Attach: menu.pdf

    One o'clock?

:func:`email_create` builds the create object, checked by
``validate_email_create``; :func:`envelope` and :func:`sent_patch` are the other
two pieces a send needs. Nothing here does I/O: uploading attachments is the
caller's, who passes their blob ids in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from jmap.core.errors import JMAPError
from jmap.core.patch import InvalidKeywordError, parse_keyword
from jmap.mail.addresses import InvalidAddressError, format_addresses, parse_addresses
from jmap.mail.render import attachments_of, body_text, html_to_text
from jmap.models.mail.create import validate_email_create

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from jmap.models.mail.objects import Email, EmailAddress

ContentType = Literal["text/plain", "text/markdown", "text/html"]

_CONTENT_TYPES: Final[tuple[ContentType, ...]] = ("text/plain", "text/markdown", "text/html")
_HEADER: Final = re.compile(r"([A-Za-z][A-Za-z0-9-]*):[ \t]?(.*)")
_MESSAGE_ID: Final = re.compile(r"<([^<>\s]+)>|([^<>\s]+)")
_RELATIVE: Final = re.compile(r"\+(\d+)([mhd])")
_SECONDS: Final = {"m": 60, "h": 3600, "d": 86400}
_ADDRESS_FIELDS: Final = {
    "from": "from_",
    "to": "to",
    "cc": "cc",
    "bcc": "bcc",
    "reply-to": "reply_to",
}


class DraftParseError(JMAPError, ValueError):
    """The draft text cannot be read; ``line`` says where, counting from 1."""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


@dataclass(frozen=True, slots=True)
class AttachmentSpec:
    """A file to attach: a local path, or a blob the server already holds."""

    path: str | None = None
    blob_id: str | None = None
    name: str | None = None
    type: str | None = None

    @property
    def key(self) -> str:
        """What :func:`email_create`'s ``uploaded`` is keyed by."""
        return self.path or f"blob:{self.blob_id}"


@dataclass(slots=True)
class Draft:
    from_: list[EmailAddress] = field(default_factory=lambda: [])
    to: list[EmailAddress] = field(default_factory=lambda: [])
    cc: list[EmailAddress] = field(default_factory=lambda: [])
    bcc: list[EmailAddress] = field(default_factory=lambda: [])
    reply_to: list[EmailAddress] = field(default_factory=lambda: [])
    subject: str = ""
    #: Message-IDs, without angle brackets.
    in_reply_to: list[str] = field(default_factory=lambda: [])
    references: list[str] = field(default_factory=lambda: [])
    attachments: list[AttachmentSpec] = field(default_factory=lambda: [])
    #: ``2026-11-01T09:00:00Z`` to hold until then, or ``+2h`` to hold that long.
    send_at: str = ""
    content_type: ContentType = "text/plain"
    keywords: list[str] = field(default_factory=lambda: [])
    #: Any other header, by name.
    headers: dict[str, str] = field(default_factory=lambda: {})
    body: str = ""

    @property
    def recipients(self) -> list[EmailAddress]:
        return [*self.to, *self.cc, *self.bcc]


@dataclass(frozen=True, slots=True)
class Uploaded:
    """A blob uploaded for an attachment."""

    blob_id: str
    type: str
    size: int | None = None


# --------------------------------------------------------------------------- #
# The text form
# --------------------------------------------------------------------------- #
def parse_draft(text: str) -> Draft:
    """Read the text form. ``#`` lines among the headers are comments."""
    draft = Draft()
    lines = text.replace("\r\n", "\n").split("\n")
    fields: list[tuple[int, str, str]] = []
    position = 0
    for position, line in enumerate(lines):
        if not line.strip():
            break
        if line.startswith("#"):
            continue
        if line[:1] in {" ", "\t"} and fields:
            number, name, value = fields[-1]
            fields[-1] = (number, name, f"{value} {line.strip()}")
            continue
        found = _HEADER.fullmatch(line)
        if found is None:
            raise DraftParseError(f"{line!r} is not a 'Name: value' header", line=position + 1)
        fields.append((position + 1, found[1], found[2].strip()))
    else:
        position = len(lines)
    for number, name, value in fields:
        _apply(draft, number, name, value)
    draft.body = "\n".join(lines[position + 1 :]).rstrip("\n")
    return draft


def render_draft(draft: Draft, *, identities: Sequence[str] = ()) -> str:
    """The text form, ready for an editor. ``identities`` are listed in a comment."""
    lines = ["# Lines starting with # are ignored. Leave a blank line before the body."]
    if identities:
        lines.append(f"# You can send as: {', '.join(identities)}")
    lines += [
        f"From: {format_addresses(draft.from_)}",
        f"To: {format_addresses(draft.to)}",
        f"Cc: {format_addresses(draft.cc)}",
        f"Bcc: {format_addresses(draft.bcc)}",
        f"Subject: {draft.subject}",
    ]
    optional = [
        ("Reply-To", format_addresses(draft.reply_to)),
        ("In-Reply-To", " ".join(f"<{i}>" for i in draft.in_reply_to)),
        ("References", " ".join(f"<{i}>" for i in draft.references)),
        ("Send-At", draft.send_at),
        ("Keywords", ", ".join(draft.keywords)),
        ("Content-Type", "" if draft.content_type == "text/plain" else draft.content_type),
        *draft.headers.items(),
    ]
    lines += [f"{name}: {value}" for name, value in optional if value]
    lines += [f"Attach: {_attachment_line(spec)}" for spec in draft.attachments]
    return "\n".join([*lines, "", draft.body]) + "\n"


def draft_from_email(email: Email) -> Draft:
    """An existing draft back in editable form, its attachments named by blob."""
    body = body_text(email)
    return Draft(
        from_=list(email.from_ or []),
        to=list(email.to or []),
        cc=list(email.cc or []),
        bcc=list(email.bcc or []),
        reply_to=list(email.reply_to or []),
        subject=email.subject or "",
        in_reply_to=list(email.in_reply_to or []),
        references=list(email.references or []),
        attachments=[
            AttachmentSpec(blob_id=part.blob_id, name=part.name, type=part.type)
            for part in attachments_of(email)
        ],
        keywords=[k for k in email.keywords or {} if not k.startswith("$")],
        content_type="text/html" if body.source == "html" else "text/plain",
        body=body.text,
    )


def _apply(draft: Draft, number: int, name: str, value: str) -> None:
    key = name.lower()
    try:
        if key in _ADDRESS_FIELDS:
            setattr(draft, _ADDRESS_FIELDS[key], parse_addresses(value))
        elif key == "subject":
            draft.subject = value
        elif key in {"in-reply-to", "references"}:
            ids = [a or b for a, b in _MESSAGE_ID.findall(value)]
            setattr(draft, "in_reply_to" if key == "in-reply-to" else "references", ids)
        elif key == "attach":
            if value:
                draft.attachments.append(_attachment(value))
        elif key == "send-at":
            if value:
                hold(value, now=datetime.now(UTC))  # checked now, not at send time
            draft.send_at = value
        elif key == "keywords":
            draft.keywords = [parse_keyword(k.strip()) for k in value.split(",") if k.strip()]
        elif key == "content-type":
            draft.content_type = _content_type(value)
        elif value:
            draft.headers[name] = value
    except (InvalidAddressError, InvalidKeywordError, DraftParseError) as error:
        raise DraftParseError(f"{name}: {error}", line=number) from error


def _attachment(value: str) -> AttachmentSpec:
    target, *options = [part.strip() for part in value.split(";")]
    params: dict[str, str] = {}
    for option in options:
        key, equals, value = option.partition("=")
        if equals:
            params[key.strip().lower()] = value.strip()
    name, kind = params.get("name") or None, params.get("type") or None
    if target.startswith("blob:"):
        return AttachmentSpec(blob_id=target[5:], name=name, type=kind)
    return AttachmentSpec(path=target, name=name, type=kind)


def _attachment_line(spec: AttachmentSpec) -> str:
    parts = [spec.path or f"blob:{spec.blob_id}"]
    parts += [f"name={spec.name}"] if spec.name else []
    parts += [f"type={spec.type}"] if spec.type else []
    return "; ".join(parts)


def _content_type(value: str) -> ContentType:
    for known in _CONTENT_TYPES:
        if value.lower() == known:
            return known
    raise DraftParseError(f"use one of {', '.join(_CONTENT_TYPES)}, not {value!r}")


# --------------------------------------------------------------------------- #
# What the server gets
# --------------------------------------------------------------------------- #
def email_create(
    draft: Draft,
    *,
    mailbox_id: str,
    uploaded: Mapping[str, Uploaded] | None = None,
    html_renderer: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """The ``Email/set`` create object for ``draft``, filed in ``mailbox_id`` as a draft.

    ``uploaded`` maps each attachment's :attr:`AttachmentSpec.key` to its blob.
    A Markdown body needs ``html_renderer``; it goes out as HTML with the
    Markdown source as the plain-text alternative.
    """
    create: dict[str, Any] = {
        "mailboxIds": {mailbox_id: True},
        "keywords": {"$draft": True, "$seen": True, **dict.fromkeys(draft.keywords, True)},
        "subject": draft.subject,
    }
    for wire, addresses in (
        ("from", draft.from_),
        ("to", draft.to),
        ("cc", draft.cc),
        ("bcc", draft.bcc),
        ("replyTo", draft.reply_to),
    ):
        if addresses:
            create[wire] = [address.to_wire() for address in addresses]
    if draft.in_reply_to:
        create["inReplyTo"] = list(draft.in_reply_to)
    if draft.references:
        create["references"] = list(draft.references)
    for name, value in draft.headers.items():
        create[f"header:{name}:asText"] = value
    create.update(_body(draft, html_renderer))
    if draft.attachments:
        create["attachments"] = [_part(spec, uploaded or {}) for spec in draft.attachments]
    validate_email_create(create)
    return create


def _body(draft: Draft, html_renderer: Callable[[str], str] | None) -> dict[str, Any]:
    text, html = draft.body, None
    if draft.content_type == "text/markdown":
        if html_renderer is None:
            raise DraftParseError("a Markdown body needs an HTML renderer")
        html = html_renderer(draft.body)
    elif draft.content_type == "text/html":
        text, html = html_to_text(draft.body), draft.body
    body: dict[str, Any] = {
        "textBody": [{"partId": "text", "type": "text/plain"}],
        "bodyValues": {"text": {"value": text}},
    }
    if html is not None:
        body["htmlBody"] = [{"partId": "html", "type": "text/html"}]
        body["bodyValues"]["html"] = {"value": html}
    return body


def _part(spec: AttachmentSpec, uploaded: Mapping[str, Uploaded]) -> dict[str, Any]:
    blob = uploaded.get(spec.key)
    if blob is None and spec.blob_id is None:
        raise DraftParseError(f"{spec.path} was not uploaded")
    part: dict[str, Any] = {
        "blobId": blob.blob_id if blob else spec.blob_id,
        "type": spec.type or (blob.type if blob else "application/octet-stream"),
        "disposition": "attachment",
    }
    name = spec.name or (spec.path.replace("\\", "/").rsplit("/", 1)[-1] if spec.path else None)
    if name:
        part["name"] = name
    return part


def hold(send_at: str, *, now: datetime) -> tuple[str, str, float]:
    """The FUTURERELEASE parameter for ``send_at`` (RFC 4865): name, value, seconds.

    ``+30m``, ``+2h`` and ``+1d`` hold for that long (``HOLDFOR``); a date and
    time holds until then (``HOLDUNTIL``), in UTC unless it says otherwise.
    """
    relative = _RELATIVE.fullmatch(send_at.strip())
    if relative:
        seconds = int(relative[1]) * _SECONDS[relative[2]]
        return "HOLDFOR", str(seconds), float(seconds)
    try:
        when = datetime.fromisoformat(send_at.strip().replace("Z", "+00:00"))
    except ValueError:
        raise DraftParseError(f"{send_at!r} is not +2h, +1d or a date and time") from None
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    if when <= now:
        raise DraftParseError(f"{send_at} is in the past")
    until = (when - now) / timedelta(seconds=1)
    return "HOLDUNTIL", when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), until


def envelope(sender: str, draft: Draft, *, now: datetime) -> dict[str, Any] | None:
    """The SMTP envelope a held send needs; ``None`` when the draft is not held.

    Without an envelope the server derives one from the headers, which is what
    an ordinary send wants. A hold has to name the parameter, so it names
    every recipient too.
    """
    if not draft.send_at:
        return None
    name, value, _ = hold(draft.send_at, now=now)
    recipients = dict.fromkeys(address.email for address in draft.recipients if address.email)
    return {
        "mailFrom": {"email": sender, "parameters": {name: value}},
        "rcptTo": [{"email": email} for email in recipients],
    }


def sent_patch(*, drafts_id: str | None, sent_id: str | None) -> dict[str, Any]:
    """What a sent draft becomes: read, no longer a draft, moved to Sent.

    Applied by the server through ``onSuccessUpdateEmail``, so only when the
    submission succeeds. Leave ``sent_id`` out for a server that files sent
    mail itself.
    """
    patch: dict[str, Any] = {"keywords/$draft": None, "keywords/$seen": True}
    if drafts_id and sent_id and drafts_id != sent_id:
        patch[f"mailboxIds/{drafts_id}"] = None
        patch[f"mailboxIds/{sent_id}"] = True
    return patch
