"""The command tree: one module per noun, mounted on the root by :func:`register`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jmap.cli.errors import UsageError

if TYPE_CHECKING:
    import typer

    from jmap.cli.context import CLIContext


def register(app: typer.Typer) -> None:
    """Mount every noun on ``app``."""
    from jmap.cli.commands import account, api, mailbox, server

    app.add_typer(account.app, name="account", help="Named accounts: servers and credentials.")
    app.add_typer(mailbox.app, name="mailbox", help="List and inspect mailboxes.")
    app.add_typer(server.app, name="server", help="The server's session and capabilities.")
    app.add_typer(api.app, name="api", help="Raw JMAP method calls.")


def cli(ctx: typer.Context) -> CLIContext:
    """The run's :class:`CLIContext`, which the root callback put on the root context."""
    return cast("CLIContext", ctx.find_root().obj)


def json_argument(text: str | None, context: CLIContext) -> dict[str, Any]:
    """A JSON object given inline, as ``@FILE``, or as ``-`` for stdin."""
    if text is None:
        return {}
    source = text
    if text == "-":
        source = context.stdin.read()
    elif text.startswith("@"):
        source = Path(text[1:]).expanduser().read_text(encoding="utf-8")
    try:
        value = json.loads(source)
    except ValueError as error:
        raise UsageError(f"the arguments are not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise UsageError("the arguments must be a JSON object")
    return cast("dict[str, Any]", value)
