"""``jmap contact edit``: a card's wire JSON in your editor, sent back as a patch."""

from __future__ import annotations

import json
from typing import Annotated, Any, cast

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.cards import Card, card_type, display_name, find_cards
from jmap.cli.commands.contact import app
from jmap.cli.context import CLIContext
from jmap.cli.editor import edit_text, editor_command
from jmap.cli.errors import CLIError, UsageError


@app.command()
def edit(
    ctx: typer.Context,
    contact: Annotated[str, typer.Argument(help="The card: an id, or an email address.")],
) -> None:
    """Edit a contact card's JSON in your editor; only what you change is sent."""
    context = cli(ctx)
    client, kind = card_type(context, "get", "set")
    [card] = find_cards(context, kind, [contact])
    patch = top_level_patch(card, _edited(context, card))
    if not patch:
        raise CLIError("the contact was not changed")
    with client.batch() as batch:
        handle = batch.add(kind.method("set"), {"update": {card["id"]: patch}})
    report_set(context, handle.result, f"updated {display_name({**card, **patch})}")


def top_level_patch(old: Card, new: Card) -> dict[str, Any]:
    """The properties that differ, whole: a new value, or ``None`` for one removed.

    Whole properties rather than paths inside them: a card's maps are keyed by
    ids local to the card, and replacing the map keeps them as the person left them.
    """
    if new.get("id", old["id"]) != old["id"]:
        raise UsageError("a card's id cannot change")
    patch: dict[str, Any] = {key: value for key, value in new.items() if old.get(key) != value}
    patch.update({key: None for key in old if key not in new})
    patch.pop("id", None)
    return patch


def _edited(context: CLIContext, card: Card) -> Card:
    """The card as the person saved it, asking again while it is not a JSON object."""
    original = json.dumps(card, indent=2, ensure_ascii=False) + "\n"
    text = original
    command = editor_command(context)
    while True:
        edited = edit_text(command, text, suffix=".json")
        if edited == original:
            raise CLIError("the contact was not changed")
        try:
            value = json.loads(edited)
        except ValueError as error:
            problem = f"the card is not valid JSON: {error}"
        else:
            if isinstance(value, dict):
                return cast("Card", value)
            problem = "the card must be a JSON object"
        again = context.prompter.interactive and context.prompter.confirm(
            f"{problem}. Edit it again?", default=True
        )
        if not again:
            raise UsageError(problem)
        text = edited
