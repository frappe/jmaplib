"""``jmap share list`` and ``jmap share notifications``: what is shared, and what changed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap import sharing
from jmap.cli.commands import cli, raise_first, report_set
from jmap.cli.commands.principal import labels
from jmap.cli.commands.share import Accounts, Target, app
from jmap.cli.errors import NotFoundError
from jmap.cli.output import Column, View, wire_of
from jmap.models.principals import ShareNotification

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext

_PROPERTIES = ["id", "name", "shareWith", "myRights", "isSubscribed"]


@app.command("list")
def list_(
    ctx: typer.Context,
    target: Annotated[
        str,
        typer.Argument(
            metavar="[KIND|OBJECT]",
            help="A kind (mailbox, calendar, addressbook, file), or one: mailbox:ID.",
        ),
    ] = "mailbox",
) -> None:
    """List what you share of one kind, or who has access to one object."""
    context = cli(ctx)
    found = Target.parse(target, whole_kind=True)
    method = f"{found.kind.type_name}/get"
    client = context.require(method)
    object_id = found.object_id(context) if found.reference else None
    with client.batch() as batch:
        ids = None if object_id is None else [object_id]
        handle = batch.add(method, {"ids": ids, "properties": _PROPERTIES})
    items: list[dict[str, Any]] = wire_of(handle.result)["list"]
    accounts = Accounts.of(client, found.kind)
    if object_id is None:
        _shared(context, [item for item in items if item.get("shareWith")], accounts)
    elif not items:
        raise NotFoundError(f"no {target}")
    else:
        _holders(context, items[0], accounts)


@app.command()
def notifications(
    ctx: typer.Context,
    clear: Annotated[bool, typer.Option("--clear", help="Delete them all.")] = False,
) -> None:
    """List the changes others made to your access, newest first."""
    context = cli(ctx)
    if clear:
        _clear(context)
        return
    client = context.require("ShareNotification/query", "ShareNotification/get")
    with client.batch() as batch:
        found = batch.add(
            "ShareNotification/query", {"sort": [{"property": "created", "isAscending": False}]}
        )
        notes = batch.add("ShareNotification/get", {"ids": found.ref_ids()})
    raise_first(found, notes)
    columns = [
        Column("CREATED", "created"),
        Column("BY", "changedBy", lambda i: _by(i.get("changedBy"))),
        Column("TYPE", "objectType"),
        Column("NAME", "name"),
        Column("CHANGE", "newRights", _change),
    ]
    context.renderer.render(View(wire_of(notes.result)["list"], columns))


def _clear(context: CLIContext) -> None:
    client = context.require("ShareNotification/query", "ShareNotification/set")
    context.confirm("Delete every share notification?")
    with client.batch() as batch:
        found = batch.add("ShareNotification/query", {})
        handle = batch.add("ShareNotification/set", {"destroy": found.ref_ids()})
    raise_first(found, handle)
    report_set(context, handle.result, "deleted {n} notification(s)")


def _shared(context: CLIContext, items: list[dict[str, Any]], accounts: Accounts) -> None:
    names = _names(context, [key for item in items for key in item["shareWith"]], accounts)

    def holders(item: Mapping[str, Any]) -> str:
        shares: dict[str, Any] = item["shareWith"]
        return "; ".join(
            f"{names.get(key, key)}: {_rights(value)}" for key, value in shares.items()
        )

    columns = [
        Column("ID", "id"),
        Column("NAME", "name"),
        Column("SHARED WITH", "shareWith", holders),
    ]
    context.renderer.render(View(items, columns))


def _holders(context: CLIContext, item: dict[str, Any], accounts: Accounts) -> None:
    """One row per principal; JSON is the object itself."""
    if context.options.json_mode:
        context.renderer.render(View([item], [], single=True))
        return
    shares: dict[str, Any] = item.get("shareWith") or {}
    if not shares:
        context.renderer.note(f"{item.get('name') or item['id']} is not shared that you can see")
    names = _names(context, list(shares), accounts)
    rows = [{"id": key, "rights": value} for key, value in shares.items()]
    columns = [
        Column("PRINCIPAL", "id"),
        Column("NAME", "id", lambda row: names.get(str(row["id"]), "")),
        Column("RIGHTS", "rights", lambda row: _rights(row["rights"])),
    ]
    context.renderer.render(View(rows, columns))


def _names(context: CLIContext, ids: list[str], accounts: Accounts) -> dict[str, str]:
    if context.options.json_mode:
        return {}
    return labels(context.client(), sorted(set(ids)), accounts.principals)


def _rights(rights: Any) -> str:
    return ", ".join(sorted(name.removeprefix("may") for name in sharing.held(rights))) or "none"


def _by(entity: Any) -> str:
    who: dict[str, Any] = entity or {}
    return str(who.get("name") or who.get("email") or "")


def _change(item: Mapping[str, Any]) -> str:
    note = ShareNotification.from_wire(item)
    if note.is_revocation:
        return "revoked"
    gained = [name.removeprefix("may") for name in sorted(note.gained())]
    if note.is_grant:
        return "granted " + ", ".join(gained)
    lost = [name.removeprefix("may") for name in sorted(note.lost())]
    return " ".join([*(f"+{name}" for name in gained), *(f"-{name}" for name in lost)])
