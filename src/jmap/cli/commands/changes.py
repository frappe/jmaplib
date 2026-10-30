"""``jmap changes TYPE``: what changed in one data type since a state.

Without ``--since`` it prints the type's current state, to keep for next time.
With it, ``Foo/changes`` is followed page by page until the server has no
more, and the pages are merged into one ``Foo/changes`` response. ``--fetch``
then gets the created and updated objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

import typer

from jmap.cli.commands import cli
from jmap.cli.context import CLIContext
from jmap.cli.output import Column, View, wire_of
from jmap.sync import ChangeSet, ChangeStream

#: Fields one of which names an object well enough for a table, by type.
_LABELS = ("name", "subject", "title", "email", "uid")


def changes(
    ctx: typer.Context,
    type_name: Annotated[str, typer.Argument(metavar="TYPE", help="A data type, such as Email.")],
    since: Annotated[
        str | None, typer.Option(help="The state to start from; the current one if not given.")
    ] = None,
    max_changes: Annotated[
        int | None, typer.Option("--max", min=1, help="At most this many ids per page.")
    ] = None,
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Also get the created and updated objects.")
    ] = False,
) -> None:
    """List the ids created, updated and destroyed since a state."""
    context = cli(ctx)
    if since is None:
        _current_state(context, type_name)
        return
    client = context.require(f"{type_name}/changes", *([f"{type_name}/get"] if fetch else []))
    stream = ChangeStream(client, type_name)
    stream.seed(since)
    found = stream.catch_up(max_changes=max_changes)
    if fetch:
        _fetch(context, found)
    else:
        _show(context, found, since)
    context.renderer.note(f"new state: {found.new_state}")


def _current_state(context: CLIContext, type_name: str) -> None:
    client = context.require(f"{type_name}/get")
    with client.batch() as batch:
        handle = batch.add(f"{type_name}/get", {"ids": []})
    response = wire_of(handle.result)
    if context.options.json_mode:
        context.renderer.json(response)
        return
    context.out.file.write(f"{response['state']}\n")


def _show(context: CLIContext, found: ChangeSet, since: str) -> None:
    if context.options.json_mode:
        # The pages merged, in the wire shape of one Foo/changes response.
        context.renderer.json(
            {
                "accountId": context.client().default_account,
                "oldState": since,
                "newState": found.new_state,
                "hasMoreChanges": False,
                "created": found.created,
                "updated": found.updated,
                "destroyed": found.destroyed,
            }
        )
        return
    context.renderer.render(View(_rows(found), [Column("CHANGE", "change"), Column("ID", "id")]))


def _fetch(context: CLIContext, found: ChangeSet) -> None:
    client = context.client()
    with client.batch() as batch:
        handle = batch.add(f"{found.type_name}/get", {"ids": found.touched})
    objects: list[dict[str, Any]] = wire_of(handle.result)["list"]
    if context.options.json_mode:
        context.renderer.json(objects)
        if found.destroyed:
            context.renderer.note(f"destroyed: {', '.join(found.destroyed)}")
        return
    by_id = {item["id"]: item for item in objects}
    rows = [{**by_id.get(row["id"], {}), **row} for row in _rows(found)]
    columns = [Column("CHANGE", "change"), Column("ID", "id"), Column("SUMMARY", "", _label)]
    context.renderer.render(View(rows, columns))


def _rows(found: ChangeSet) -> list[dict[str, Any]]:
    return [
        {"change": change, "id": object_id}
        for change, ids in (
            ("created", found.created),
            ("updated", found.updated),
            ("destroyed", found.destroyed),
        )
        for object_id in ids
    ]


def _label(item: Mapping[str, Any]) -> str:
    return next((str(item[key]) for key in _LABELS if item.get(key)), "")
