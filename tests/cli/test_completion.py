"""Tab completion in your shell, and ``jmap completion``.

The shell asks by running the command with ``_JMAP_COMPLETE`` and the words so
far in ``_TYPER_COMPLETE_ARGS``; the answer goes to stdout, which ``capsys``
reads. zsh's form is the easiest to read: one ``"value":"help"`` per line.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from jmap.cli import completers
from jmap.cli.app import run
from jmap.cli.completers import MAIL_SORT, comma_list

if TYPE_CHECKING:
    from collections.abc import Callable

    from jmap.cli.context import CLIContext
    from jmap.testing import FakeJMAPServer


@pytest.fixture
def complete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_context: Callable[..., CLIContext],
) -> Callable[[str], str]:
    def ask(line: str) -> str:
        monkeypatch.setenv("_JMAP_COMPLETE", "complete_zsh")
        monkeypatch.setenv("_TYPER_COMPLETE_ARGS", line)
        assert run([], context=make_context()) == 0
        return capsys.readouterr().out

    return ask


class TestCommands:
    def test_nouns_and_verbs(self, complete):
        assert '"mailbox"' in complete("jmap ma")
        assert '"mail"' in complete("jmap ma")
        assert '"list"' in complete("jmap mail li")

    def test_options(self, complete):
        assert '"--in"' in complete("jmap ls --")


class TestValues:
    pytestmark = pytest.mark.usefixtures("account")

    def test_accounts(self, complete):
        assert '"work"' in complete("jmap -a ")
        assert '"work"' in complete("jmap account show w")

    def test_mailboxes_from_the_server_and_the_roles(self, complete):
        found = complete("jmap ls --in ")
        assert '"Archive/2025"' in found
        assert '"inbox":"role"' in found
        assert '"Archive/2025"' in complete("jmap move 3 Archive/")
        assert '"archive":"role"' in complete("jmap move 3 arch")

    def test_after_messages_only_a_word_is_a_mailbox(self, complete):
        assert "Archive" not in complete("jmap move 3")
        assert "Archive" not in complete("jmap move id:")
        assert '"Archive"' in complete("jmap mail copy 1-2 Ar")

    def test_roles_alone_when_the_server_fails(self, complete, server):
        server.fail("Mailbox/get", "serverFail")
        found = complete("jmap ls --in ")
        assert '"inbox":"role"' in found
        assert "Archive/2025" not in found

    def test_roles_alone_when_the_server_is_slow(self, complete, server, monkeypatch):
        monkeypatch.setattr(completers, "DEADLINE", 0.05)
        real = server.handlers["Mailbox/get"]

        def slow(args: dict[str, Any], srv: FakeJMAPServer) -> Any:
            time.sleep(0.5)
            return real(args, srv)

        server.handle("Mailbox/get", slow)
        assert "Archive/2025" not in complete("jmap ls --in ")

    def test_columns_and_sort_keys(self, complete):
        assert '"id,subject"' in complete("jmap ls --columns id,su")
        assert '"date,-size"' in complete("jmap ls --sort date,")


def test_no_password_is_asked_for(complete, secrets, account):
    """A missing secret ends the server's part at once, rather than prompting."""
    secrets.delete(account, "password")
    found = complete("jmap ls --in ")
    assert '"inbox":"role"' in found
    assert "Archive/2025" not in found


def test_no_account_configured(complete):
    assert complete("jmap -a ") == "_files\n"


def test_a_broken_configuration_file(complete, environ):
    from jmap.cli.paths import config_path

    path = config_path(environ)
    path.parent.mkdir(parents=True)
    path.write_text("not = [valid", encoding="utf-8")
    assert complete("jmap -a ") == "_files\n"


def test_outside_a_command():
    """A context the command did not make, as a test of Typer might pass."""

    class Root:
        obj = None
        params: ClassVar[dict[str, Any]] = {}

        def find_root(self) -> Root:
            return self

    assert completers.accounts(Root(), "") == []  # type: ignore[arg-type]
    assert [value for value, _ in completers.mailboxes(Root(), "")] == list(completers.ROLES)  # type: ignore[arg-type]


def test_the_silent_prompter_refuses_everything():
    silent = completers._Silent()
    assert not silent.interactive
    for ask in (silent.ask, silent.secret, silent.confirm):
        with pytest.raises(completers._NoPromptError):
            ask("password?")


class TestCommaList:
    def test_the_last_item(self):
        complete = comma_list(["id", "subject", "size"])
        assert complete("s") == ["subject", "size"]
        assert complete("id,s") == ["id,subject", "id,size"]
        assert complete("subject,") == ["subject,id", "subject,size"]

    def test_signed(self):
        complete = comma_list(MAIL_SORT, signed=True)
        assert complete("-da") == ["-date"]
        assert complete("-date,-su") == ["-date,-subject"]
        assert "-date" in complete("")


class TestCompletionCommand:
    def test_the_script_for_a_shell(self, invoke):
        zsh = invoke("completion", "zsh").out
        assert zsh.startswith("#compdef jmap\n")
        assert "_JMAP_COMPLETE=complete_zsh" in zsh
        assert "complete --command jmap" in invoke("completion", "fish").out

    def test_an_unknown_shell(self, invoke):
        assert invoke("completion", "tcsh").code == 2

    def test_under_the_name_it_was_run_as(self, make_context, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/jmap-cli", "completion", "bash"])
        context = make_context()
        assert run(context=context) == 0
        script = context.out.file.getvalue()
        assert "complete -o default -F _jmap_cli_completion jmap-cli" in script
        assert "_JMAP_CLI_COMPLETE" in script

    def test_python_dash_m_is_jmap(self, make_context, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/x/jmap/__main__.py", "completion", "bash"])
        context = make_context()
        run(context=context)
        assert "_JMAP_COMPLETE" in context.out.file.getvalue()

    def test_install(self, invoke, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = invoke("completion", "bash", "--install")
        assert result.code == 0, result.err
        assert "added bash completion to" in result.err
        assert "jmap" in (tmp_path / ".bashrc").read_text(encoding="utf-8")
