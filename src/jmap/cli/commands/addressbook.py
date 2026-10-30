"""``jmap addressbook``: the collections contact cards live in (RFC 9610 §2)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of

app: typer.Typer = typer.Typer(no_args_is_help=True)

Book = Annotated[str, typer.Argument(help="The address book, by name or id.")]

_COLUMNS = [
    Column("ID", "id"),
    Column("NAME", "name"),
    Column("DEFAULT", "isDefault", lambda book: "yes" if book.get("isDefault") else ""),
    Column("SUBSCRIBED", "isSubscribed", lambda book: "yes" if book.get("isSubscribed") else ""),
    Column("DESCRIPTION", "description"),
]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List your address books, the default marked."""
    context = cli(ctx)
    context.renderer.render(View(all_books(context), _COLUMNS))


@app.command()
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="The new address book's name.")],
    description: Annotated[str | None, typer.Option(help="What it holds.")] = None,
    default: Annotated[
        bool, typer.Option("--default", help="Make it the default address book.")
    ] = False,
) -> None:
    """Create an address book."""
    context = cli(ctx)
    book: dict[str, Any] = {"name": name}
    if description is not None:
        book["description"] = description
    arguments: dict[str, Any] = {"create": {"new": book}}
    if default:
        # The server ignores this, rather than failing the create, if it refuses.
        arguments["onSuccessSetIsDefault"] = "#new"
    _set(context, arguments, f"created the address book {name}")


@app.command()
def rename(
    ctx: typer.Context,
    book: Book,
    name: Annotated[str, typer.Argument(help="The new name.")],
) -> None:
    """Rename an address book."""
    context = cli(ctx)
    found = find_book(context, book)
    _set(context, {"update": {found["id"]: {"name": name}}}, f"renamed {found['name']} to {name}")


@app.command()
def delete(
    ctx: typer.Context,
    book: Book,
    with_contacts: Annotated[
        bool, typer.Option("--with-contacts", help="Also destroy the cards in it.")
    ] = False,
) -> None:
    """Delete an address book. The server refuses one that holds cards, unless --with-contacts."""
    context = cli(ctx)
    found = find_book(context, book)
    extra = " and the contacts in it" if with_contacts else ""
    context.confirm(f"Delete the address book {found['name']}{extra}?")
    arguments = {"destroy": [found["id"]], "onDestroyRemoveContents": with_contacts}
    _set(context, arguments, f"deleted {found['name']}")


def all_books(context: CLIContext) -> list[dict[str, Any]]:
    """Every address book, in the server's sort order, then by name."""
    client = context.require("AddressBook/get")
    with client.batch() as batch:
        handle = batch.add("AddressBook/get", {"ids": None})
    books: list[dict[str, Any]] = wire_of(handle.result)["list"]
    return sorted(books, key=lambda book: (book.get("sortOrder") or 0, book.get("name") or ""))


def find_book(context: CLIContext, wanted: str) -> dict[str, Any]:
    """The address book named ``wanted``, by id first, then by name in any case."""
    books = all_books(context)
    for book in books:
        if book.get("id") == wanted:
            return book
    named = [book for book in books if str(book.get("name", "")).lower() == wanted.lower()]
    if len(named) > 1:
        raise UsageError(f"{len(named)} address books are named {wanted!r}", hint="give its id")
    if not named:
        raise NotFoundError(f"no address book {wanted!r}", hint="see 'jmap addressbook list'")
    return named[0]


def book_for(context: CLIContext, wanted: str | None) -> dict[str, Any]:
    """The address book ``wanted`` names, or else the default one."""
    if wanted is not None:
        return find_book(context, wanted)
    books = all_books(context)
    chosen = next((book for book in books if book.get("isDefault")), None) or next(
        iter(books), None
    )
    if chosen is None:
        raise NotFoundError(
            "this account has no address book", hint="create one with 'jmap addressbook create'"
        )
    return chosen


def _set(context: CLIContext, arguments: dict[str, Any], done: str) -> None:
    client = context.require("AddressBook/set")
    with client.batch() as batch:
        handle = batch.add("AddressBook/set", arguments)
    report_set(context, handle.result, done)
