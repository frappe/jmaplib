"""Addresses as people write them, and as JMAP wants them.

``Alice <alice@example.com>, bob@example.com`` becomes two ``EmailAddress``
objects. The standard library's parser does the splitting, which copes with
quoted names holding commas; this module refuses what it cannot vouch for.
"""

from __future__ import annotations

import re
from email.utils import formataddr, getaddresses
from typing import TYPE_CHECKING, Final

from jmap.core.errors import JMAPError
from jmap.models.mail.objects import EmailAddress

if TYPE_CHECKING:
    from collections.abc import Iterable

#: A deliberately loose addr-spec: something, one @, a domain with a dot or not.
#: Strict RFC 5321 validation is the server's job; this catches typos.
_ADDR_SPEC: Final = re.compile(r"[^@\s<>,;]+@[^@\s<>,;]+")
_EMPTY_ENTRIES: Final = re.compile(r"(?:,\s*)+,")


class InvalidAddressError(JMAPError, ValueError):
    """Text that is not a list of email addresses."""


def parse_addresses(text: str) -> list[EmailAddress]:
    """Every address in ``text``, comma separated, with or without a display name."""
    # Empty entries (``a@x, , b@y``) are dropped first: the strict parser of
    # newer Pythons answers a whole list it cannot read with one empty pair.
    cleaned = _EMPTY_ENTRIES.sub(",", text).strip(" ,")
    if not cleaned:
        return []
    addresses: list[EmailAddress] = []
    for name, email in getaddresses([cleaned]):
        if not _ADDR_SPEC.fullmatch(email):
            shown = f"{name} <{email}>" if name else email
            raise InvalidAddressError(f"{(shown.strip() or text)!r} is not an email address")
        # Unset rather than None, so the wire form has no "name": null.
        addresses.append(
            EmailAddress(name=name, email=email) if name else EmailAddress(email=email)
        )
    return addresses


def format_address(address: EmailAddress) -> str:
    """``Name <email>``, quoting the name where it needs it, or the bare address."""
    return formataddr((address.name or "", address.email or ""))


def format_addresses(addresses: Iterable[EmailAddress]) -> str:
    return ", ".join(format_address(address) for address in addresses)
