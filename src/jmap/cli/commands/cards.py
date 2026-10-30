"""Contact cards of either shape: RFC 9610's ``ContactCard`` and the legacy ``Contact``.

A server offers one or both. RFC 9610 wins when both are there. The legacy type
is Fastmail's and Cyrus's own, never standardised, so it is read best-effort:
the same columns, from the fields those servers are known to send.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, wire_of
from jmap.core.ids import parse_id
from jmap.core.narrow import as_list, is_list, is_object

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient

Card = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CardType:
    """Which data type holds the contacts, and how to search it."""

    name: str
    legacy: bool
    #: The filter condition that finds a card by email address.
    email_filter: str

    def method(self, verb: str) -> str:
        return f"{self.name}/{verb}"


RFC: Final = CardType("ContactCard", legacy=False, email_filter="email")
#: Fastmail's and Cyrus's ``Contact/query`` know no ``email`` condition.
LEGACY: Final = CardType("Contact", legacy=True, email_filter="text")


def card_type(context: CLIContext, *verbs: str) -> tuple[JMAPClient, CardType]:
    """The client and the contacts type it offers, once ``verbs`` are known to work."""
    client = context.client()
    if not client.capabilities.supports("ContactCard/get") and client.capabilities.supports(
        "Contact/get"
    ):
        return context.require(*map(LEGACY.method, verbs)), LEGACY
    return context.require(*map(RFC.method, verbs)), RFC


def find_cards(context: CLIContext, kind: CardType, refs: list[str]) -> list[Card]:
    """The cards ``refs`` name: listing numbers, ids, or addresses that match one card each."""
    client = context.client()
    addresses = [ref for ref in refs if "@" in ref]
    ids: list[str] = [
        context.contact_cursor.resolve(int(ref)) if ref.isdigit() else parse_id(ref)
        for ref in refs
        if "@" not in ref
    ]
    with client.batch() as batch:
        lookups = {
            address: batch.add(kind.method("query"), {"filter": {kind.email_filter: address}})
            for address in addresses
        }
    for address, handle in lookups.items():
        ids.append(_only_match(address, [str(found) for found in handle.result.ids]))
    with client.batch() as batch:
        handle = batch.add(kind.method("get"), {"ids": list(dict.fromkeys(ids))})
    cards: list[Card] = wire_of(handle.result)["list"]
    missing = set(ids) - {card["id"] for card in cards}
    if missing:
        raise NotFoundError(f"no contact {', '.join(sorted(missing))}")
    return cards


def _only_match(address: str, ids: list[str]) -> str:
    if not ids:
        raise NotFoundError(f"no contact has the address {address}")
    if len(ids) > 1:
        raise UsageError(f"{len(ids)} contacts have {address}: {', '.join(ids)}", hint="give an id")
    return ids[0]


# --------------------------------------------------------------------------- #
# Reading a card, whichever shape it has
# --------------------------------------------------------------------------- #
def display_name(card: Mapping[str, Any]) -> str:
    """The name to show: the full name, its parts, an organization, else an address."""
    name = card.get("name")
    if is_object(name):
        shown = str(name.get("full") or "") or " ".join(_components(name))
        if shown:
            return shown
    legacy = " ".join(str(card[key]) for key in ("firstName", "lastName") if card.get(key))
    return legacy or organization(card) or next(iter(emails(card)), "")


def emails(card: Mapping[str, Any]) -> list[str]:
    return _entries(card.get("emails"), "address")


def phones(card: Mapping[str, Any]) -> list[str]:
    return _entries(card.get("phones"), "number")


def organization(card: Mapping[str, Any]) -> str:
    found = _entries(card.get("organizations"), "name")
    return ", ".join(found) or str(card.get("company") or "")


def addresses(card: Mapping[str, Any]) -> list[str]:
    return [_address(item) for item in _objects(card.get("addresses"))]


def notes(card: Mapping[str, Any]) -> list[str]:
    value = card.get("notes")
    if isinstance(value, str):
        return [value] if value else []  # legacy: one string
    return _entries(value, "note")


#: What a listing shows of a card.
COLUMNS: Final = [
    Column("ID", "id"),
    Column("NAME", "name", display_name),
    Column("EMAIL", "emails", lambda card: next(iter(emails(card)), "")),
    Column("PHONE", "phones", lambda card: next(iter(phones(card)), "")),
    Column("ORGANIZATION", "organizations", organization),
]


def _entries(value: Any, key: str) -> list[str]:
    """The ``key`` of each entry in a JSContact map, or ``value`` of each in a legacy list."""
    wanted = "value" if is_list(value) else key
    return [str(item[wanted]) for item in _objects(value) if item.get(wanted)]


def _objects(value: Any) -> list[dict[str, Any]]:
    """The objects in a JSContact map (its values) or a legacy list (its items)."""
    items = list(value.values()) if is_object(value) else value
    return [item for item in as_list(items) if is_object(item)] if is_list(items) else []


#: The parts of a legacy postal address, in the order they are written.
_LEGACY_ADDRESS: Final = ("street", "locality", "region", "postcode", "country")


def _address(item: dict[str, Any]) -> str:
    if item.get("full"):
        return str(item["full"])
    parts = _components(item) + [str(item[key]) for key in _LEGACY_ADDRESS if item.get(key)]
    return ", ".join(parts)


def _components(value: dict[str, Any]) -> list[str]:
    """The text of a name's or an address's components, without the separators."""
    return [
        str(part["value"])
        for part in _objects(value.get("components"))
        if part.get("kind") != "separator" and part.get("value")
    ]
