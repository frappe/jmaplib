"""``jmap shell``: the loop, its built-ins, the prompt, completion, and line editing."""

from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from jmap.cli.app import run
from jmap.cli.completion import PromptToolkitLines, ShellCompleter
from jmap.cli.context import CLIContext, GlobalFlags
from jmap.cli.shell import Shell, StdinLines, line_source
from tests.fake.mailstore import email

from .conftest import text_of

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx


class ScriptedLines:
    """Lines from a list; an exception in the list is raised instead."""

    def __init__(self, *lines: str | BaseException) -> None:
        self.lines = list(lines)
        self.prompts: list[str] = []

    def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        line = self.lines.pop(0)
        if isinstance(line, BaseException):
            raise line
        return line


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def mail(store):
    store.add(
        email("e1", "Invoice", received="2026-10-28T09:00:00Z"),
        email("e2", "Lunch", received="2026-10-29T09:00:00Z", mailboxes=("y25",)),
    )
    return store


def shell(
    make_context: Callable[..., CLIContext], *lines: str | BaseException
) -> tuple[int, str, str, ScriptedLines, CLIContext]:
    context = make_context()
    script = ScriptedLines(*lines)
    context.lines = script
    code = run(["shell"], context=context)
    return code, text_of(context.out.file), text_of(context.err.file), script, context


@pytest.mark.usefixtures("account")
class TestLoop:
    def test_commands_share_one_connection(self, make_context, server):
        fetched: list[str] = []

        def count(request: httpx.Request) -> None:
            if request.url.path.endswith("/jmap/session"):
                fetched.append(str(request.url))

        server.intercept = count
        code, out, _, _, _ = shell(make_context, "ls", "read 1 --no-mark-seen", "server echo")
        assert code == 0
        assert "Invoice" in out
        assert '"ping": "jmap"' in out
        assert len(fetched) == 1

    def test_cd_pwd_and_listing_the_current_mailbox(self, make_context):
        _, out, _, script, _ = shell(
            make_context,
            "cd archive/2025",
            "pwd",
            "ls",
            "cd ..",
            "pwd",
            "cd",
            "pwd",
            "cd archive",
            "cd ..",
            "pwd",
        )
        assert out.splitlines() == [
            "Archive/2025",
            "1\tN\t2026-10-29T09:00:00Z\tBob\tLunch\t1012",
            "Archive",
            "Inbox",
            "Inbox",
        ]
        assert script.prompts[2] == "alice@work:Archive/2025> "

    def test_dot_is_the_current_mailbox(self, make_context, store):
        shell(make_context, "cd archive/2025", "ls", "move 1 .", "move e1 .")
        assert set(store.emails["e1"]["mailboxIds"]) == {"y25"}

    def test_errors_are_reported_and_the_loop_goes_on(self, make_context):
        code, out, err, _, _ = shell(make_context, "cd nowhere", "'open", "ls --in junk", "pwd")
        assert code == 0
        assert "no mailbox 'nowhere'" in err
        assert "cannot read that line" in err
        assert out.endswith("Inbox\n")

    def test_the_exit_status_is_the_last_one(self, make_context):
        assert shell(make_context, "read 9")[0] == 9
        assert shell(make_context, "read 9", "exit")[0] == 9
        assert shell(make_context, "read 9", "# just a comment", "", "quit")[0] == 9

    def test_ctrl_c_drops_the_line(self, make_context):
        code, out, _, _, _ = shell(make_context, KeyboardInterrupt(), "pwd")
        assert code == 0
        assert out == "Inbox\n"

    def test_ctrl_c_in_a_built_in(self, make_context, monkeypatch):
        def interrupted(self, words):
            raise KeyboardInterrupt

        monkeypatch.setattr(Shell, "_pwd", interrupted)
        assert shell(make_context, "pwd")[0] == 130

    def test_help(self, make_context, capsys):
        _, out, _, _, _ = shell(make_context, "help mail")
        assert "Shell: cd MAILBOX" in out
        assert "search" in capsys.readouterr().out

    def test_refresh_and_a_stale_session(self, make_context, server):
        _, _, _, _, context = shell(make_context, "ls", "refresh")
        context.client().session_stale = True
        before = len(server.requests)
        session = Shell(context, context.flags)
        session.execute("server echo")
        assert context.client().session_stale is False
        assert len(server.requests) == before + 1

    def test_use_another_account(self, make_context, server, environ):
        server.accounts["s"] = {"name": "Sales", "accountCapabilities": {}}
        _, _, _, script, context = shell(make_context, "use work:s", "server echo", "use a b")
        assert script.prompts[1] == "work:Inbox> "
        assert script.prompts[2] == "alice@work[s]:Inbox> "
        assert "use ACCOUNT" in text_of(context.err.file)

    def test_shell_flags_apply_to_every_line(self, make_context):
        context = make_context()
        context.lines = ScriptedLines("server echo")
        flags = ["--no-keyring", "--experimental", "--insecure", "--config", "x.toml"]
        session = Shell(
            context, GlobalFlags(no_keyring=True, experimental=True, insecure=True, config="x.toml")
        )
        assert session.globals() == flags


def test_the_prompt_without_an_account(make_context):
    _, _, err, script, _ = shell(make_context, "pwd")
    assert script.prompts[0] == "jmap> "
    assert err == ""


@pytest.mark.usefixtures("account")
def test_the_prompt_for_a_nameless_user(make_context, server):
    server.username = ""
    _, _, _, script, _ = shell(make_context, "ls", "pwd")
    assert script.prompts[1] == "work:Inbox> "


@pytest.mark.usefixtures("account")
class TestCompletion:
    def candidates(
        self,
        make_context: Callable[..., CLIContext],
        words: list[str],
        current: str = "",
        *,
        load: bool = False,
    ) -> list[str]:
        context = make_context()
        if load:
            context.mailboxes()
        return Shell(context, context.flags).candidates(words, current)

    def test_the_first_word(self, make_context):
        found = self.candidates(make_context, [])
        assert {"mail", "ls", "cd", "exit", "shell"} <= set(found)

    def test_a_verb(self, make_context):
        found = self.candidates(make_context, ["mail"])
        assert "search" in found
        assert "cd" not in found

    def test_options_but_not_global_ones(self, make_context):
        found = self.candidates(make_context, ["mail", "list"], "--")
        assert "--unread" in found
        assert "--json" not in found

    def test_nothing_after_a_full_command(self, make_context):
        assert self.candidates(make_context, ["mail", "list", "x"]) == []
        assert self.candidates(make_context, ["nope", "x"]) != []

    def test_mailboxes_once_loaded(self, make_context):
        assert self.candidates(make_context, ["cd"]) == []
        found = self.candidates(make_context, ["ls", "--in"], load=True)
        assert {"Archive/2025", "archive", "inbox"} <= set(found)
        assert "Inbox" in self.candidates(make_context, ["mailbox", "show"], load=True)
        assert "show" in self.candidates(make_context, ["mailbox"], load=True)

    def test_the_prompt_toolkit_completer(self):
        completer = ShellCompleter(lambda words, current: ["list", "ls", "search"])
        found = list(completer.get_completions(Document("mail l"), None))  # type: ignore[arg-type]
        assert [c.text for c in found] == ["list", "ls"]
        assert found[0].start_position == -1
        assert len(list(completer.get_completions(Document("mail "), None))) == 3  # type: ignore[arg-type]
        assert list(completer.get_completions(Document('mail "x'), None)) == []  # type: ignore[arg-type]


class TestLines:
    def test_stdin_lines_prompt_only_on_a_terminal(self):
        err = io.StringIO()
        piped = StdinLines(io.StringIO("ls\n"), Console(file=err))
        assert piped.read("> ") == "ls"
        assert err.getvalue() == ""
        with pytest.raises(EOFError):
            piped.read("> ")
        typed = StdinLines(TTY("pwd\r\n"), Console(file=err))
        assert typed.read("jmap> ") == "pwd"
        assert err.getvalue() == "jmap> "

    def test_prompt_toolkit_lines(self, tmp_path):
        with create_pipe_input() as pipe:
            pipe.send_text("ls\n")
            lines = PromptToolkitLines(
                tmp_path / "state" / "history", lambda w, c: [], input=pipe, output=DummyOutput()
            )
            assert lines.read("> ") == "ls"
        assert "ls" in (tmp_path / "state" / "history").read_text()

    def test_line_source_choices(self, make_context, tmp_path, monkeypatch):
        context = make_context()
        assert isinstance(line_source(context, tmp_path / "h", lambda w, c: []), StdinLines)
        context.stdin = TTY()
        monkeypatch.setattr("jmap.cli.completion.PromptSession", lambda **kw: kw)
        assert isinstance(line_source(context, tmp_path / "h", lambda w, c: []), PromptToolkitLines)
        monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        assert isinstance(line_source(context, tmp_path / "h", lambda w, c: []), StdinLines)
        assert "install prompt_toolkit" in text_of(context.err.file)

    def test_the_shell_command_picks_a_line_source(self, make_context, monkeypatch):
        context = make_context(stdin="pwd\n")
        assert run(["shell"], context=context) == 0
        assert text_of(context.out.file) == "Inbox\n"
