"""Values for Tab completion in your shell: accounts, mailboxes, columns and sort keys.

Typer calls these while it completes a command line, before anything runs. A
completer must never ask for anything or keep the shell waiting: one that
prompts for a password, or hangs on a slow server, freezes the line being
typed. So prompting is switched off, mailbox names come from the server only
within :data:`DEADLINE`, and any failure leaves the roles, which need no server.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Final

# Not under TYPE_CHECKING: Typer reads a completer's annotations when it runs.
import typer

from jmap.cli.context import CLIContext, GlobalFlags
from jmap.core.errors import JMAPError

#: Seconds to wait for the server's mailbox names before offering roles alone.
DEADLINE: Final = 3.0

#: Mailbox roles every reference accepts, whether or not the server is reachable.
ROLES: Final = ("inbox", "drafts", "sent", "trash", "junk", "archive", "all", "important")

#: ``--sort`` keys for mail, each also accepted with a leading ``-``.
MAIL_SORT: Final = ("date", "subject", "from", "to", "size", "sentat", "flagged", "unread")

Choices = list[tuple[str, str]]


def accounts(ctx: typer.Context, incomplete: str) -> Choices:
    """The named accounts in the configuration file, with their server."""
    context = _context(ctx)
    if context is None:
        return []
    try:
        configured = context.config.accounts
    except (JMAPError, ValueError, OSError):
        return []
    return [(name, item.url) for name, item in configured.items() if name.startswith(incomplete)]


def mailboxes(ctx: typer.Context, incomplete: str) -> Choices:
    """Mailbox paths from the server, and the roles that always work.

    Matched as typed: shells drop a completion that does not start with the word.
    """
    found: dict[str, str] = dict.fromkeys(ROLES, "role")
    context = _context(ctx)
    if context is not None:
        for path, role in _paths(context):
            found.setdefault(path, role or "mailbox")
    return [(value, help_) for value, help_ in found.items() if value.startswith(incomplete)]


def messages_then_mailbox(ctx: typer.Context, incomplete: str) -> Choices:
    """For ``move 1-3 Archive``: numbers and ids are messages, anything else the mailbox.

    The messages take every word up to the mailbox, so the mailbox never gets a
    completion turn of its own.
    """
    if incomplete[:1].isdigit() or incomplete.startswith(("id:", "msgid:", "thread:")):
        return []
    return mailboxes(ctx, incomplete)


def comma_list(names: Iterable[str], *, signed: bool = False) -> Callable[[str], list[str]]:
    """Complete the last item of a comma list, such as ``--columns id,sub``.

    ``signed`` lets an item start with ``-``, as a descending sort key does.
    """
    choices = tuple(names)

    def complete(incomplete: str) -> list[str]:
        head, comma, last = incomplete.rpartition(",")
        sign = "-" if signed and last.startswith("-") else ""
        used: set[str] = {item.lstrip("-") for item in head.split(",")} if comma else set()
        prefix = f"{head}{comma}{sign}"
        stem = last[len(sign) :]
        found = [prefix + name for name in choices if name.startswith(stem) and name not in used]
        if signed and not last:
            # The shell never asks about a word that starts with "-", so offer those here.
            found += [f"{head}{comma}-{name}" for name in choices if name not in used]
        return found

    return complete


def _context(ctx: typer.Context) -> CLIContext | None:
    """The command's context, set up as the line's global options ask, and silent."""
    root = ctx.find_root()
    context = root.obj
    if not isinstance(context, CLIContext):
        return None
    given = root.params
    context.prompter = _Silent()
    context.configure(
        GlobalFlags(
            account=given.get("account"),
            account_id=given.get("account_id"),
            no_keyring=bool(given.get("no_keyring")),
            experimental=bool(given.get("experimental")),
            insecure=bool(given.get("insecure")),
            config=given.get("config"),
        )
    )
    return context


def _paths(context: CLIContext) -> list[tuple[str, str]]:
    """Each mailbox's path and role, or nothing if the server does not answer in time."""
    found: list[tuple[str, str]] = []

    def load() -> None:
        try:
            index = context.mailboxes()
        except Exception:
            return
        for box in index.ordered():
            found.append((index.path_of(str(box["id"])), str(box.get("role") or "")))

    worker = threading.Thread(target=load, daemon=True)
    worker.start()
    worker.join(DEADLINE)
    return [] if worker.is_alive() else found


class _Silent:
    """A prompter with nobody to ask, so a missing password fails at once."""

    @property
    def interactive(self) -> bool:
        return False

    def ask(self, prompt: str, *, default: str = "") -> str:
        raise _NoPromptError(prompt)

    def secret(self, prompt: str) -> str:
        raise _NoPromptError(prompt)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        raise _NoPromptError(prompt)


class _NoPromptError(JMAPError):
    """Completion asked a question; there is no one to answer it."""
