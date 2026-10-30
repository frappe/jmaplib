"""``jmap contact``: list, show, create and delete contact cards.

``edit`` is in :mod:`jmap.cli.commands.contact_edit`, and ``import`` in
:mod:`jmap.cli.commands.contact_import`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.addressbook import all_books, book_for, find_book
from jmap.cli.commands.cards import (
    COLUMNS,
    addresses,
    card_type,
    display_name,
    emails,
    find_cards,
    listed,
    notes,
    organization,
    phones,
)
from jmap.cli.errors import UnsupportedError, UsageError
from jmap.cli.output import Column, View
from jmap.core.narrow import is_object
from jmap.models.contacts import ContactCard
from jmap.models.jscontact import (
    EmailAddress,
    Name,
    NameComponent,
    Note,
    Organization,
    Phone,
    Title,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

app: typer.Typer = typer.Typer(no_args_is_help=True)

Contacts = Annotated[list[str], typer.Argument(help="Card ids, or email addresses.")]
One = Annotated[str, typer.Argument(help="The card: an id, or an email address.")]
BookOption = Annotated[str | None, typer.Option("--book", help="The address book, by name or id.")]

#: ``--sort`` words and the ``ContactCard/query`` property each sorts on.
SORTS: Final = {
    "created": "created",
    "updated": "updated",
    "given": "name/given",
    "surname": "name/surname",
}


@app.command("list")
def list_(
    ctx: typer.Context,
    text: Annotated[str | None, typer.Argument(help="Only cards that mention this.")] = None,
    book: BookOption = None,
    sort: Annotated[
        str | None,
        typer.Option(help="surname, given, created or updated; a leading - reverses it."),
    ] = None,
    limit: Annotated[int, typer.Option("-n", "--limit", min=1, help="Cards to show.")] = 100,
) -> None:
    """List contact cards, or those that match TEXT."""
    context = cli(ctx)
    client, kind = card_type(context, "query", "get")
    if kind.legacy and (book or sort):
        raise UnsupportedError("this server's legacy contacts have no address books or sorting")
    conditions: dict[str, Any] = {}
    if text:
        conditions["text"] = text
    if book is not None:
        conditions["inAddressBook"] = find_book(context, book)["id"]
    query: dict[str, Any] = {"limit": limit, "calculateTotal": True}
    if conditions:
        query["filter"] = conditions
    if sort:
        query["sort"] = [_comparator(sort)]
    with client.batch() as batch:
        found = batch.add(kind.method("query"), query)
        cards = batch.add(kind.method("get"), {"ids": found.ref_ids()})
    by_id = {card["id"]: card for card in listed(cards.result)}
    rows = [by_id[card_id] for card_id in found.result.ids if card_id in by_id]
    total = found.result.total
    footer = f"{len(rows)} of {total} contact(s)" if total is not None else None
    context.renderer.render(View(rows, COLUMNS, footer=footer))


@app.command()
def show(ctx: typer.Context, contact: One) -> None:
    """Show one contact card: every address, number and note."""
    context = cli(ctx)
    _, kind = card_type(context, "get")
    [card] = find_cards(context, kind, [contact])
    names = {book["id"]: book.get("name") for book in all_books(context)} if not kind.legacy else {}
    columns = [
        Column("ID", "id"),
        Column("Name", "name", display_name),
        Column("Kind", "kind"),
        Column("Emails", "emails", lambda item: ", ".join(emails(item))),
        Column("Phones", "phones", lambda item: ", ".join(phones(item))),
        Column("Organization", "organizations", organization),
        Column("Addresses", "addresses", lambda item: "; ".join(addresses(item))),
        Column("Notes", "notes", lambda item: " / ".join(notes(item))),
        Column("Address books", "addressBookIds", lambda item: _book_names(item, names)),
        Column("UID", "uid"),
    ]
    context.renderer.render(View([card], columns, single=True))


@app.command()
def create(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Option(help="The full name, as shown.")] = None,
    given: Annotated[str | None, typer.Option(help="The given (first) name.")] = None,
    surname: Annotated[str | None, typer.Option(help="The surname (family name).")] = None,
    email: Annotated[list[str] | None, typer.Option(help="An email address (repeatable).")] = None,
    phone: Annotated[list[str] | None, typer.Option(help="A phone number (repeatable).")] = None,
    org: Annotated[str | None, typer.Option(help="The organization.")] = None,
    title: Annotated[str | None, typer.Option(help="The job title.")] = None,
    note: Annotated[str | None, typer.Option(help="A note.")] = None,
    kind: Annotated[
        str | None, typer.Option(help="individual (the default), org, group, location...")
    ] = None,
    book: BookOption = None,
) -> None:
    """Create a contact card, in --book or the default address book."""
    context = cli(ctx)
    new = NewCard(name, given, surname, email or [], phone or [], org, title, note, kind)
    if not (name or given or surname or org or email):
        raise UsageError("a contact needs a name, an organization or an email address")
    client = context.require("ContactCard/set")
    chosen = book_for(context, book)
    card = new.to_wire(str(chosen["id"]))
    with client.batch() as batch:
        handle = batch.add("ContactCard/set", {"create": {"new": card}})
    report_set(context, handle.result, f"created {display_name(card)} in {chosen['name']}")


@app.command()
def delete(ctx: typer.Context, contacts: Contacts) -> None:
    """Delete contact cards, after you confirm."""
    context = cli(ctx)
    client, kind = card_type(context, "get", "set")
    cards = find_cards(context, kind, contacts)
    names = ", ".join(display_name(card) or card["id"] for card in cards)
    context.confirm(f"Delete {len(cards)} contact(s): {names}?")
    with client.batch() as batch:
        handle = batch.add(kind.method("set"), {"destroy": [card["id"] for card in cards]})
    report_set(context, handle.result, "deleted {n} contact(s)")


@dataclass(frozen=True, slots=True)
class NewCard:
    """What ``contact create`` was told, and the JSContact card it makes."""

    name: str | None
    given: str | None
    surname: str | None
    emails: list[str]
    phones: list[str]
    org: str | None
    title: str | None
    note: str | None
    kind: str | None

    def to_wire(self, book_id: str) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "at_type": "Card",
            "version": "1.0",
            "uid": f"urn:uuid:{uuid.uuid4()}",
            "address_book_ids": {book_id: True},
            **self._name(),
        }
        if self.emails:
            numbered = enumerate(self.emails, 1)
            fields["emails"] = {f"e{n}": EmailAddress(address=a) for n, a in numbered}
        if self.phones:
            fields["phones"] = {f"p{n}": Phone(number=p) for n, p in enumerate(self.phones, 1)}
        if self.org is not None:
            fields["organizations"] = {"o1": Organization(name=self.org)}
        if self.title is not None:
            at = {"organization_id": "o1"} if self.org is not None else {}
            fields["titles"] = {"t1": Title(name=self.title, **at)}
        if self.note is not None:
            fields["notes"] = {"n1": Note(note=self.note)}
        if self.kind is not None:
            fields["kind"] = self.kind.lower()
        return ContactCard(**fields).to_wire()

    def _name(self) -> dict[str, Name]:
        """The ``name``, if one is given; the full name falls back to the parts."""
        parts = [
            NameComponent(kind=kind, value=value)
            for kind, value in (("given", self.given), ("surname", self.surname))
            if value
        ]
        shown = self.name or " ".join(str(part.value) for part in parts)
        if not shown:
            return {}
        return {"name": Name(full=shown, components=parts) if parts else Name(full=shown)}


def _comparator(text: str) -> dict[str, Any]:
    word = text.lstrip("-").lower()
    if word not in SORTS:
        raise UsageError(f"cannot sort by {word!r}", hint=f"use one of {', '.join(SORTS)}")
    return {"property": SORTS[word], "isAscending": not text.startswith("-")}


def _book_names(card: Mapping[str, Any], names: dict[str, Any]) -> str:
    books = card.get("addressBookIds")
    ids = [key for key, value in books.items() if value] if is_object(books) else []
    return ", ".join(str(names.get(book_id) or book_id) for book_id in ids)
