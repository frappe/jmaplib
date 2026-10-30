"""Replies and forwards, and which identity to send them from."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from jmap.mail.addresses import format_addresses
from jmap.mail.compose import AttachmentSpec, Draft
from jmap.mail.render import attachments_of, quote
from jmap.models.mail.objects import EmailAddress

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from jmap.models.mail.objects import Email, Identity

_RE: Final = re.compile(r"^\s*re\s*:", re.IGNORECASE)
_FWD: Final = re.compile(r"^\s*(fwd?|fw)\s*:", re.IGNORECASE)


def reply_draft(
    original: Email,
    *,
    identity: Identity | None,
    reply_all: bool = False,
    quote_text: str | None = None,
    me: Iterable[str] = (),
) -> Draft:
    """A reply to ``original``, threaded by In-Reply-To and References.

    It goes to Reply-To when there is one, else to the sender. ``reply_all``
    adds the other recipients, leaving out ``me`` (the account's addresses) and
    anyone already in To. With ``quote_text``, the body quotes it below an
    attribution line.
    """
    mine = {address.lower() for address in me}
    if identity is not None and identity.email:
        mine.add(identity.email.lower())
    to = list(original.reply_to or original.from_ or [])
    cc: list[EmailAddress] = []
    if reply_all:
        taken = mine | {address.email.lower() for address in to if address.email}
        for address in (*(original.to or []), *(original.cc or [])):
            email = (address.email or "").lower()
            if email and email not in taken:
                cc.append(address)
                taken.add(email)
    subject = original.subject or ""
    message_ids = list(original.message_id or [])
    return Draft(
        from_=_sender(identity),
        to=to,
        cc=cc,
        subject=subject if _RE.match(subject) else f"Re: {subject}",
        in_reply_to=message_ids,
        references=[*(original.references or []), *message_ids],
        body=_quoted(original, quote_text) if quote_text is not None else "",
    )


def forward_draft(
    original: Email,
    *,
    identity: Identity | None,
    quote_text: str | None = None,
    as_attachment: bool = False,
) -> Draft:
    """``original`` forwarded: inline with its attachments, or whole as a ``.eml``.

    Attachments are re-attached by blob id, so nothing is downloaded.
    """
    subject = original.subject or ""
    if as_attachment:
        attachments = [
            AttachmentSpec(
                blob_id=original.blob_id,
                name=f"{subject or 'message'}.eml",
                type="message/rfc822",
            )
        ]
        body = ""
    else:
        attachments = [
            AttachmentSpec(blob_id=part.blob_id, name=part.name, type=part.type)
            for part in attachments_of(original)
            if part.blob_id
        ]
        body = _forwarded(original, quote_text or "")
    return Draft(
        from_=_sender(identity),
        subject=subject if _FWD.match(subject) else f"Fwd: {subject}",
        references=[*(original.references or []), *(original.message_id or [])],
        attachments=attachments,
        body=body,
    )


def pick_identity(
    identities: Sequence[Identity],
    *,
    wanted: str | None = None,
    original: Email | None = None,
    username: str | None = None,
) -> Identity | None:
    """The identity to send from.

    ``wanted`` (an id or an address) when given, and ``None`` if it matches
    none. Otherwise, for a reply, the identity the original was sent to; then
    the one matching the account's ``username``; then the first.
    """
    if wanted is not None:
        return next(
            (i for i in identities if wanted.lower() in {i.id, (i.email or "").lower()}), None
        )
    if original is not None:
        addressed = {
            (address.email or "").lower()
            for address in (*(original.to or []), *(original.cc or []), *(original.bcc or []))
        }
        found = next((i for i in identities if (i.email or "").lower() in addressed), None)
        if found is not None:
            return found
    if username:
        found = next((i for i in identities if (i.email or "").lower() == username.lower()), None)
        if found is not None:
            return found
    return identities[0] if identities else None


def _sender(identity: Identity | None) -> list[EmailAddress]:
    if identity is None or not identity.email:
        return []
    return [EmailAddress(name=identity.name or None, email=identity.email)]


def _quoted(original: Email, text: str) -> str:
    who = format_addresses(original.from_ or []) or "someone"
    when = original.sent_at or original.received_at or ""
    attribution = f"On {when}, {who} wrote:" if when else f"{who} wrote:"
    return f"\n\n{attribution}\n{quote(text.strip())}\n"


def _forwarded(original: Email, text: str) -> str:
    lines = [
        "",
        "",
        "---------- Forwarded message ----------",
        f"From: {format_addresses(original.from_ or [])}",
        f"Date: {original.sent_at or original.received_at or ''}",
        f"Subject: {original.subject or ''}",
        f"To: {format_addresses(original.to or [])}",
    ]
    if original.cc:
        lines.append(f"Cc: {format_addresses(original.cc)}")
    return "\n".join([*lines, "", text.strip(), ""])
