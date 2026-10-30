"""Asking the person at the terminal, and the SRV trust question."""

from __future__ import annotations

import getpass
import io

from rich.console import Console

from jmap.cli.prompts import TerminalPrompter, srv_confirmation
from jmap.discovery import SRVTarget

from .conftest import ScriptedPrompter


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def prompter(stdin: io.StringIO) -> tuple[TerminalPrompter, io.StringIO]:
    err = io.StringIO()
    return TerminalPrompter(Console(file=err), stdin), err


class TestTerminalPrompter:
    def test_a_pipe_is_not_interactive_and_is_read_directly(self):
        asker, err = prompter(io.StringIO("bob\ny\n"))
        assert not asker.interactive
        assert asker.ask("Name") == "bob"
        assert asker.confirm("Sure?") is True
        assert "Name" in err.getvalue()

    def test_a_terminal_reads_with_input(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *args: "n")
        asker, _ = prompter(TTY())
        assert asker.interactive
        assert asker.confirm("Sure?", default=True) is False
        assert asker.ask("Name", default="x") == "n"

    def test_secrets_use_getpass(self, monkeypatch):
        monkeypatch.setattr(getpass, "getpass", lambda prompt, stream=None: "hunter2")
        asker, _ = prompter(TTY())
        assert asker.secret("Password") == "hunter2"


class TestSrvConfirmation:
    def test_a_trusted_host_needs_no_question(self):
        asker = ScriptedPrompter()
        confirm = srv_confirmation(asker, ["Mail.Other.Net."])
        assert confirm(SRVTarget(host="mail.other.net"))
        assert asker.asked == []

    def test_nobody_to_ask_means_no(self):
        confirm = srv_confirmation(ScriptedPrompter(interactive=False), [])
        assert not confirm(SRVTarget(host="mail.other.net"))

    def test_otherwise_ask(self):
        asker = ScriptedPrompter(confirms=[True])
        assert srv_confirmation(asker, [])(SRVTarget(host="mail.other.net", port=8443))
        assert "mail.other.net:8443" in asker.asked[0]
