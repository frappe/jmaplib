"""``jmap shell``: one connection, many commands.

Each line runs through the same command tree as a one-shot ``jmap`` call, with
the shell's account flags in front, so everything that works on the command
line works here. What the shell adds is state that outlives a line: the open
connection, the mailbox index, the cursor, and the current mailbox (``cd``),
which listing commands use when no mailbox is named.

Errors are reported and the loop goes on; ``exit`` or end of input leaves.
"""

from __future__ import annotations

import importlib.util
import shlex
from typing import TYPE_CHECKING, Final, Protocol, cast

import typer
from typer.core import TyperCommand, TyperGroup

from jmap.cli.app import RESERVED, app, run
from jmap.cli.errors import CLIError, ExitCode, report

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import TextIO

    from rich.console import Console

    from jmap.cli.context import CLIContext, GlobalFlags

#: Words the shell handles itself rather than passing to the command tree.
BUILTINS: Final = ("exit", "quit", "help", "cd", "pwd", "use", "refresh")

#: Where a mailbox is wanted: after these options, and as these commands' arguments.
_MAILBOX_OPTIONS: Final = frozenset({"--in", "--from"})
_MAILBOX_COMMANDS: Final = frozenset({"cd", "move", "copy", "mailbox"})


class LineSource(Protocol):
    def read(self, prompt: str) -> str:
        """One line; ``EOFError`` at the end, ``KeyboardInterrupt`` on Ctrl-C."""
        ...


class StdinLines:
    """Plain line reading, for pipes and for machines without prompt_toolkit."""

    def __init__(self, stdin: TextIO, err: Console) -> None:
        self._stdin = stdin
        self._err = err

    def read(self, prompt: str) -> str:
        if self._stdin.isatty():  # a piped script wants no prompts in its output
            self._err.print(prompt, end="", markup=False, highlight=False)
        line = self._stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\r\n")


class Shell:
    """The read-run loop around one :class:`CLIContext`."""

    context: CLIContext
    account: str | None
    account_id: str | None
    #: The current mailbox's path, for the prompt.
    where: str
    #: The exit status of the last line, which leaving the shell returns.
    last_exit: int

    def __init__(self, context: CLIContext, flags: GlobalFlags) -> None:
        self.context = context
        self.account = flags.account
        self.account_id = flags.account_id
        self._fixed = _fixed_args(flags)
        self.where = "Inbox"
        self.last_exit = 0

    def run(self, lines: LineSource) -> int:
        while True:
            try:
                line = lines.read(self.prompt())
            except KeyboardInterrupt:
                continue  # Ctrl-C drops the line, not the shell
            except EOFError:
                break
            try:
                if self.execute(line) is None:
                    break
            except KeyboardInterrupt:
                self.last_exit = ExitCode.INTERRUPTED  # in a built-in; commands catch their own
        self.context.close()
        return self.last_exit

    def execute(self, line: str) -> int | None:
        """Run one line; ``None`` means leave the shell."""
        try:
            words = shlex.split(line, comments=True)
        except ValueError as error:
            return self._fail(CLIError(f"cannot read that line: {error}"))
        if not words:
            return self.last_exit
        if words[0] in {"exit", "quit"}:
            return None
        builtin = getattr(self, f"_{words[0]}", None) if words[0] in BUILTINS else None
        try:
            if builtin is not None:
                builtin(words[1:])
                self.last_exit = ExitCode.OK
            else:
                self._refresh_if_stale()
                self.last_exit = run([*self.globals(), *words], context=self.context, close=False)
        except Exception as error:
            return self._fail(error)
        return self.last_exit

    def globals(self) -> list[str]:
        """The global options every line runs with: the shell's account, and its flags."""
        chosen = ["-a", self.account] if self.account else []
        chosen += ["--account-id", self.account_id] if self.account_id else []
        return [*chosen, *self._fixed]

    def prompt(self) -> str:
        context = self.context
        try:
            name = context.account.name
        except CLIError:
            return "jmap> "
        client = context.connected
        who = name
        if client is not None:
            user = client.session.username.split("@")[0]
            who = f"{user}@{name}" if user else name
            if self.account_id:
                who += f"[{client.default_account}]"
        return f"{who}:{self.where}> "

    def candidates(self, words: list[str], current: str) -> list[str]:
        """What may come next after ``words``, for completion."""
        if words and (words[-1] in _MAILBOX_OPTIONS or _wants_mailbox(words)):
            return self._mailboxes()
        node: TyperGroup | TyperCommand = _tree()
        for word in (w for w in words if not w.startswith("-")):
            if not isinstance(node, TyperGroup) or word not in node.commands:
                break
            # Every command in this tree is one of Typer's two classes.
            node = cast("TyperGroup | TyperCommand", node.commands[word])
        if current.startswith("-"):
            return sorted(opt for param in node.params for opt in param.opts if opt not in RESERVED)
        if not isinstance(node, TyperGroup):
            return []
        names = [name for name in node.commands if not name.startswith("-")]
        return sorted({*names, *(BUILTINS if not words else ())})

    # -- built-ins ------------------------------------------------------- #
    def _help(self, words: list[str]) -> None:
        run([*words, "--help"], context=self.context, close=False)
        self.context.out.print(
            "Shell: cd MAILBOX, cd .., pwd, use ACCOUNT[:JMAP-ACCOUNT], refresh, exit",
            markup=False,
        )

    def _cd(self, words: list[str]) -> None:
        context = self.context
        target = words[0] if words else ""
        if target in {"", "/", "~"}:
            context.current_mailbox, self.where = None, "Inbox"
            return
        if target == "..":
            parent = self.where.rpartition("/")[0]
            if not parent:
                context.current_mailbox, self.where = None, "Inbox"
                return
            target = parent
        found = context.mailbox(target)
        context.current_mailbox = f"id:{found['id']}"
        self.where = context.mailboxes().path_of(str(found["id"]))

    def _pwd(self, words: list[str]) -> None:
        self.context.out.file.write(self.where + "\n")

    def _use(self, words: list[str]) -> None:
        if len(words) != 1:
            raise CLIError("use ACCOUNT, or use ACCOUNT:JMAP-ACCOUNT")
        name, _, jmap_account = words[0].partition(":")
        self.account, self.account_id = name, jmap_account or None
        self.context.current_mailbox, self.where = None, "Inbox"
        self.context.use_account(name)

    def _refresh(self, words: list[str]) -> None:
        client = self.context.client()
        client.refresh_session()
        self.context.forget_mailboxes()

    # -- helpers ---------------------------------------------------------- #
    def _refresh_if_stale(self) -> None:
        client = self.context.connected
        if client is not None and client.session_stale:
            client.refresh_session()
            self.context.forget_mailboxes()

    def _fail(self, error: BaseException) -> int:
        self.last_exit = report(
            error,
            self.context.err,
            json_mode=self.context.options.json_mode,
            verbose=self.context.flags.verbose,
        )
        return self.last_exit

    def _mailboxes(self) -> list[str]:
        index = self.context.loaded_mailboxes
        if index is None:
            return []
        paths = [index.path_of(str(box["id"])) for box in index.ordered()]
        roles = [str(box["role"]) for box in index.ordered() if box.get("role")]
        return sorted({*paths, *roles})


def line_source(
    context: CLIContext, history: Path, candidates: Callable[[list[str], str], list[str]]
) -> LineSource:
    """prompt_toolkit's editor on a terminal; plain reading otherwise."""
    if context.stdin.isatty():
        if importlib.util.find_spec("prompt_toolkit") is not None:
            from jmap.cli.completion import PromptToolkitLines

            return PromptToolkitLines(history, candidates)
        context.renderer.note("install prompt_toolkit for history and completion")
    return StdinLines(context.stdin, context.err)


def _fixed_args(flags: GlobalFlags) -> list[str]:
    """The shell's own flags other than the account, repeated on every line."""
    args: list[str] = []
    for enabled, option in (
        (flags.no_keyring, "--no-keyring"),
        (flags.experimental, "--experimental"),
        (flags.insecure, "--insecure"),
    ):
        if enabled:
            args.append(option)
    if flags.config:
        args += ["--config", flags.config]
    return args


def _wants_mailbox(words: list[str]) -> bool:
    return words[0] in _MAILBOX_COMMANDS and (words[0] != "mailbox" or len(words) >= 2)


def _tree() -> TyperGroup:
    tree = typer.main.get_command(app)
    assert isinstance(tree, TyperGroup)
    return tree
