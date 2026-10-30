"""``jmap watch``: follow the server's event source until interrupted.

Each ``StateChange`` says which types moved, never what changed. By default
``watch`` prints one line per type that moved. ``--exec`` runs a command for
each change instead. ``--new-mail`` follows ``Email/changes`` and prints one
line per new message, which ``--match`` narrows with a search.

The stream is a sequence, so ``--json`` prints one JSON object per line, as
``--jsonl`` does.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from jmap.cli import format as fmt
from jmap.cli.commands import cli, comma_list
from jmap.cli.errors import UsageError
from jmap.cli.output import wire_of
from jmap.core.errors import MethodError
from jmap.mail.query import parse, to_filter
from jmap.models.push import StateChange
from jmap.push import EventSourceClient, Ping, UnknownPushTypeError
from jmap.sync.changes import is_resync_error

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient
    from jmap.models.push import CalendarAlert

    Event = StateChange | CalendarAlert | Ping

#: What a one-line summary of a new message needs.
_PROPERTIES: Final = ["id", "receivedAt", "from", "subject", "mailboxIds"]


def watch(
    ctx: typer.Context,
    types: Annotated[
        list[str] | None,
        typer.Option(help="Types to watch, such as Email,Mailbox; every type if not given."),
    ] = None,
    ping: Annotated[
        int, typer.Option(min=0, help="Ask for a keep-alive this often, in seconds.")
    ] = 30,
    command: Annotated[
        str | None,
        typer.Option("--exec", help="Run this for each change, with the event on stdin."),
    ] = None,
    new_mail: Annotated[
        bool, typer.Option("--new-mail", help="Print one line for each new message.")
    ] = False,
    match: Annotated[
        str | None, typer.Option(help="With --new-mail: only messages this search finds.")
    ] = None,
) -> None:
    """Print changes as the server announces them, until Ctrl-C."""
    context = cli(ctx)
    if match is not None and not new_mail:
        raise UsageError("--match needs --new-mail")
    wanted = comma_list(types) or None
    tracker = NewMail.start(context, match) if new_mail else None
    if tracker is not None and wanted is not None and "Email" not in wanted:
        wanted.append("Email")
    try:
        source = EventSourceClient(context.client(), types=wanted, ping=ping)
    except UnknownPushTypeError as error:
        raise UsageError(str(error)) from error
    watcher = Watcher(context, shlex.split(command) if command else None, tracker)
    with closing(source.listen()) as events:
        for event in events:
            watcher.handle(event)


class Watcher:
    """Turns each event into output, a command run, or new-mail lines."""

    def __init__(
        self, context: CLIContext, command: list[str] | None, tracker: NewMail | None
    ) -> None:
        self.context = context
        self.command = command
        self.tracker = tracker

    def handle(self, event: Event) -> None:
        if isinstance(event, Ping):
            return
        if self.tracker is not None:
            for email in self.tracker.check(event):
                self._line(email, _summary(email))
        elif self.command is None:
            self._show(event)
        if self.command is not None:
            self._run(self.command, event)

    def _show(self, event: StateChange | CalendarAlert) -> None:
        if self.context.options.json_mode:
            self._line(event, "")
            return
        if isinstance(event, StateChange):
            for account, states in event.changed.items():
                for name, state in states.items():
                    self._line(event, f"{account}\t{name}\t{state}")
            return
        self._line(event, f"{event.account_id}\tCalendarAlert\t{event.calendar_event_id}")

    def _line(self, value: Any, text: str) -> None:
        line = (
            json.dumps(wire_of(value), ensure_ascii=False)
            if self.context.options.json_mode
            else text
        )
        file = self.context.out.file
        file.write(line + "\n")
        file.flush()  # a pipe reading the stream should see each line as it comes

    def _run(self, command: list[str], event: StateChange | CalendarAlert) -> None:
        changed = event.changed if isinstance(event, StateChange) else {}
        types = sorted(event.types()) if isinstance(event, StateChange) else ["CalendarAlert"]
        environment = {
            **self.context.environ,
            "JMAP_ACCOUNT": self.context.account.name,
            "JMAP_TYPES": ",".join(types),
            "JMAP_STATES": json.dumps(changed),
        }
        try:
            done = subprocess.run(
                command,
                input=json.dumps(wire_of(event)),
                text=True,
                env=environment,
                check=False,
            )
        except OSError as error:
            raise UsageError(f"cannot run {command[0]}: {error.strerror or error}") from error
        if done.returncode:
            self.context.renderer.note(f"{command[0]} exited {done.returncode}")


@dataclass(slots=True)
class NewMail:
    """The Email state seen so far, and the messages created after it."""

    client: JMAPClient
    account_id: str
    state: str
    #: An ``Email/query`` filter a new message must match, if any.
    match: dict[str, Any] | None

    @classmethod
    def start(cls, context: CLIContext, text: str | None) -> NewMail:
        """Note the current Email state, so only mail after it counts as new."""
        client = context.require("Email/changes", "Email/get", *(["Email/query"] if text else []))
        node = parse(text) if text else None
        match = (
            to_filter(node, mailbox_id=lambda name: str(context.mailbox(name)["id"]))
            if node
            else None
        )
        account_id, state = _current(client)
        return cls(client, account_id, state, match)

    def check(self, event: StateChange | CalendarAlert) -> list[dict[str, Any]]:
        """The messages created since the last check, if ``event`` says Email moved."""
        if not isinstance(event, StateChange):
            return []
        pushed = event.states_for(self.account_id).get("Email")
        if pushed is None or pushed == self.state:
            return []
        created = self._created()
        if self.match is None or not created:
            return created
        return self._matching(created)

    def _created(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        while True:
            with self.client.batch() as batch:
                changed = batch.add("Email/changes", {"sinceState": self.state})
                got = batch.add(
                    "Email/get", {"ids": changed.ref("/created"), "properties": _PROPERTIES}
                )
            try:
                page = wire_of(changed.result)
            except MethodError as error:
                if not is_resync_error(error):
                    raise
                # The server forgot that state: start again from now.
                self.account_id, self.state = _current(self.client)
                return found
            self.state = page["newState"]
            found += wire_of(got.result)["list"]
            if not page.get("hasMoreChanges"):
                return found

    def _matching(self, created: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Those of ``created`` the search finds.

        No filter names ids, so the search runs over mail received since the
        earliest new message, and the answer is crossed with ``created``.
        """
        earliest = min(_moment(item["receivedAt"]) for item in created)
        since = (earliest - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = {"operator": "AND", "conditions": [self.match, {"after": since}]}
        with self.client.batch() as batch:
            handle = batch.add("Email/query", {"filter": query})
        ids = set(handle.result.ids)
        return [item for item in created if item["id"] in ids]


def _current(client: JMAPClient) -> tuple[str, str]:
    """The mail account's id and its Email state now."""
    with client.batch() as batch:
        handle = batch.add("Email/get", {"ids": []})
    response = wire_of(handle.result)
    return str(response["accountId"]), str(response["state"])


def _summary(email: Mapping[str, Any]) -> str:
    return "\t".join(
        [
            email["id"],
            email.get("receivedAt", ""),
            fmt.address(email.get("from")),
            email.get("subject") or "",
        ]
    )


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
