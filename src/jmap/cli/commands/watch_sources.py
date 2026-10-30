"""Where ``jmap watch`` gets its changes when push does not reach it.

Some servers sit behind a proxy that holds an event source back: the request is
sent and nothing ever comes, not even the keep-alive ping. The listener then
redials quietly, so without help ``watch`` waits forever and says nothing.
:class:`SilenceNote` says so once, and :func:`poll` is the way around it: one
``Foo/get`` with no ids per type, every few seconds, over the plain API.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Final

from jmap.cli.errors import UsageError
from jmap.cli.output import wire_of
from jmap.models.push import StateChange
from jmap.push.eventsource import MIN_PORTABLE_PING
from jmap.push.listener import PING_TIMEOUT_SLACK

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient

#: What ``--poll`` follows when no ``--types`` are given, where the server has them.
POLLED: Final = (
    "Mailbox",
    "Email",
    "Thread",
    "EmailSubmission",
    "Identity",
    "AddressBook",
    "ContactCard",
    "Calendar",
    "CalendarEvent",
)


def poll(client: JMAPClient, types: list[str] | None, interval: float) -> Iterator[StateChange]:
    """A ``StateChange`` each time a type's state moves, checked every ``interval`` seconds."""
    names = _polled(client, types)
    seen: dict[str, str] | None = None
    while True:
        with client.batch() as batch:
            handles = {name: batch.add(f"{name}/get", {"ids": []}) for name in names}
        changed: dict[str, dict[str, str]] = {}
        states: dict[str, str] = {}
        for name, handle in handles.items():
            response: dict[str, Any] = wire_of(handle.result)
            states[name] = str(response["state"])
            if seen is not None and seen.get(name) != states[name]:
                changed.setdefault(str(response["accountId"]), {})[name] = states[name]
        if changed:
            yield StateChange(changed=changed)
        seen = states
        time.sleep(interval)


def _polled(client: JMAPClient, types: list[str] | None) -> list[str]:
    if types is None:
        return [name for name in POLLED if client.capabilities.supports(f"{name}/get")]
    missing = [name for name in types if not client.capabilities.supports(f"{name}/get")]
    if missing:
        raise UsageError(f"this server has no {', '.join(missing)}/get to poll")
    return types


class SilenceNote:
    """Say once when the event source sends nothing at all, not even a ping.

    The deadline is the listener's own: past it, the connection counts as dead.
    """

    def __init__(self, context: CLIContext, ping: int) -> None:
        seconds = max(ping, MIN_PORTABLE_PING) + PING_TIMEOUT_SLACK
        self._timer = threading.Timer(seconds, self._say, args=(context, int(seconds)))
        self._timer.daemon = True
        self._enabled = ping > 0

    def __enter__(self) -> SilenceNote:
        if self._enabled:
            self._timer.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.heard()

    def heard(self) -> None:
        """Something arrived: the event source works."""
        self._timer.cancel()

    @staticmethod
    def _say(context: CLIContext, seconds: int) -> None:
        context.renderer.note(
            f"the server's event source has sent nothing in {seconds}s, not even a ping; "
            "a proxy may be holding it back. 'jmap watch --poll 30' asks for changes instead"
        )
