"""Questions put to the person at the terminal.

Behind a protocol so tests script the answers, and so a command run without a
terminal can tell there is nobody to ask and fail with a remedy instead of
hanging on a read from a pipe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from rich.prompt import Confirm, Prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import TextIO

    from rich.console import Console

    from jmap.discovery import SRVTarget


class Prompter(Protocol):
    @property
    def interactive(self) -> bool:
        """Whether there is a person to ask."""
        ...

    def ask(self, prompt: str, *, default: str = "") -> str: ...

    def secret(self, prompt: str) -> str: ...

    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...


class TerminalPrompter:
    """Asks on stderr and reads from ``stdin``, so stdout stays clean for data."""

    def __init__(self, console: Console, stdin: TextIO) -> None:
        self._console = console
        self._stdin = stdin

    @property
    def interactive(self) -> bool:
        return self._stdin.isatty()

    def ask(self, prompt: str, *, default: str = "") -> str:
        return Prompt.ask(prompt, console=self._console, default=default, stream=self._stream)

    def secret(self, prompt: str) -> str:
        # Never given the stream: getpass would treat it as where to write.
        return Prompt.ask(prompt, console=self._console, password=True)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        return Confirm.ask(prompt, console=self._console, default=default, stream=self._stream)

    @property
    def _stream(self) -> TextIO | None:
        # rich reads with input() when given no stream, which is what a real
        # terminal wants; anything else is read directly.
        return None if self._stdin.isatty() else self._stdin


def srv_confirmation(
    prompter: Prompter,
    trusted: Iterable[str],
    on_trust: Callable[[str], None] | None = None,
) -> Callable[[SRVTarget], bool]:
    """Answer the library's SRV question from the trusted list, else ask (RFC 6186 §6).

    ``on_trust`` hears each host the person agreed to, so it can be remembered.
    """
    allowed = {host.lower().rstrip(".") for host in trusted}

    def confirm(target: SRVTarget) -> bool:
        if target.host.lower().rstrip(".") in allowed:
            return True
        if not prompter.interactive:
            return False
        agreed = prompter.confirm(
            f"DNS points this address at {target.host}:{target.port}, outside its domain. "
            "Send your credentials there?"
        )
        if agreed and on_trust is not None:
            on_trust(target.host.lower().rstrip("."))
        return agreed

    return confirm
