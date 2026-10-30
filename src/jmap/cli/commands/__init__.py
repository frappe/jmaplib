"""The command tree: one module per noun, mounted on the root by :func:`register`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

from jmap.cli.errors import PartialFailureError, UsageError
from jmap.cli.output import wire_of

if TYPE_CHECKING:
    from collections.abc import Callable

    import typer

    from jmap.cli.context import CLIContext


def register(app: typer.Typer) -> None:
    """Mount every noun on ``app``."""
    from jmap.cli.commands import (
        account,
        api,
        attachment,
        calendar,
        compose,
        config_cmd,
        draft,
        event,
        event_edit,
        file,
        freebusy,
        identity,
        mail,
        mail_files,
        mail_read,
        mail_triage,
        mailbox,
        mailbox_edit,
        server,
        shell_cmd,
        submission,
        vacation,
    )

    # Importing these is what defines their commands on mail.app and mailbox.app.
    del mail_read, mail_triage, mail_files, mailbox_edit, event_edit

    app.add_typer(account.app, name="account", help="Named accounts: servers and credentials.")
    app.add_typer(mailbox.app, name="mailbox", help="List, create and change mailboxes.")
    app.add_typer(mail.app, name="mail", help="List, search, read and file messages.")
    app.add_typer(server.app, name="server", help="The server's session and capabilities.")
    app.add_typer(api.app, name="api", help="Raw JMAP method calls.")
    app.add_typer(config_cmd.app, name="config", help="The configuration file.")
    app.add_typer(draft.app, name="draft", help="Messages saved to finish later.")
    app.add_typer(attachment.app, name="attachment", help="A message's files.")
    app.add_typer(identity.app, name="identity", help="The addresses you send from.")
    app.add_typer(submission.app, name="submission", help="Sent and held messages.")
    app.add_typer(vacation.app, name="vacation", help="The automatic reply while away.")
    app.add_typer(calendar.app, name="calendar", help="Calendars (experimental).")
    app.add_typer(event.app, name="event", help="Calendar events (experimental).")
    app.command("freebusy")(freebusy.freebusy)
    app.add_typer(file.app, name="file", help="Files stored on the server (experimental).")
    app.command("shell")(shell_cmd.shell)
    for verb in (compose.compose, compose.send, compose.reply, compose.forward):
        app.command(rich_help_panel="Mail")(verb)
    _aliases(app, mail.app)


#: The daily verbs, also at the top level: ``jmap ls`` is ``jmap mail list``.
ALIASES: Final = {
    "ls": "list",
    "search": "search",
    "read": "read",
    "thread": "thread",
    "move": "move",
    "delete": "delete",
    "archive": "archive",
    "flag": "flag",
    "unflag": "unflag",
    "seen": "seen",
    "unseen": "unseen",
}


def _aliases(app: typer.Typer, mail: typer.Typer) -> None:
    """Register each daily verb's callback a second time, at the top level."""
    callbacks: dict[str, Callable[..., Any]] = {}
    for info in mail.registered_commands:
        callback = cast("Callable[..., Any]", info.callback)
        # Typer names an unnamed command after its function, as here.
        callbacks[info.name or callback.__name__.replace("_", "-")] = callback
    for alias, verb in ALIASES.items():
        help_text = f"Alias of 'jmap mail {verb}'."
        app.command(alias, help=help_text, rich_help_panel="Mail")(callbacks[verb])


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


def report_set(context: CLIContext, result: Any, done: str) -> None:
    """Say what a ``/set`` changed, and raise for anything it refused.

    ``done`` names the change, as in ``"moved {n} message(s)"``. JSON mode
    prints the response itself. Refusals end the run with exit 8 after the
    successes are reported.
    """
    response = wire_of(result)
    if context.options.json_mode:
        context.renderer.json(response)
    changed = len(response.get("updated") or {}) + len(response.get("destroyed") or [])
    changed += len(response.get("created") or {})
    if changed:
        # Not str.format: the text holds mailbox names, which may hold braces.
        context.renderer.note(done.replace("{n}", str(changed)))
    refused: dict[str, dict[str, Any]] = {
        **(response.get("notCreated") or {}),
        **(response.get("notUpdated") or {}),
        **(response.get("notDestroyed") or {}),
    }
    if refused:
        reasons = "; ".join(
            f"{key}: {error.get('type')}"
            + (f" ({error['description']})" if error.get("description") else "")
            for key, error in refused.items()
        )
        raise PartialFailureError(f"{len(refused)} not changed: {reasons}")
