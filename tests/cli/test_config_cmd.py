"""``jmap config path``, ``edit`` and ``set``."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from jmap.cli.config import load_config
from jmap.cli.paths import config_path


class Editor:
    """Stands in for the editor: records the command, then writes ``text``."""

    def __init__(self, text: str | None = None, status: int = 0) -> None:
        self.text = text
        self.status = status
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.text is not None:
            with open(command[-1], "w", encoding="utf-8") as file:
                file.write(self.text)
        return subprocess.CompletedProcess(command, self.status)


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch) -> Editor:
    fake = Editor()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_path(invoke, environ):
    assert invoke("config", "path").out == f"{config_path(environ)}\n"


class TestEdit:
    def test_creates_the_file_and_checks_it(self, invoke, editor, environ):
        result = invoke("config", "edit", env={"VISUAL": "code --wait", "EDITOR": "nano"})
        assert result.code == 0
        assert editor.commands == [["code", "--wait", str(config_path(environ))]]
        assert "is valid" in result.err

    def test_editor_order(self, invoke, editor, environ):
        invoke("config", "edit", env={"EDITOR": "nano"})
        invoke("config", "set", "defaults.editor", "hx")
        invoke("config", "edit", env={"EDITOR": "nano"})
        invoke("config", "edit", env={"EDITOR": ""})
        firsts = [command[0] for command in editor.commands]
        assert firsts == ["nano", "hx", "hx"]

    def test_the_fallback(self, invoke, editor, monkeypatch):
        invoke("config", "edit")
        assert editor.commands[0][0] in {"vi", "notepad"}

    def test_a_mistake_is_reported_now(self, invoke, editor):
        editor.text = "colour = 'red'\n"
        assert invoke("config", "edit").code == 10

    def test_a_broken_file_can_still_be_opened(self, invoke, editor, environ):
        path = config_path(environ)
        path.parent.mkdir(parents=True)
        path.write_text("= broken")
        editor.text = 'default_account = "work"\n'
        assert invoke("config", "edit", env={"EDITOR": "nano"}).code == 0
        assert editor.commands[0][0] == "nano"

    def test_a_failing_editor(self, invoke, editor):
        editor.status = 3
        result = invoke("config", "edit")
        assert result.code == 1
        assert "status 3" in result.err


class TestSet:
    def test_values_are_toml_or_text(self, invoke, environ, account):
        assert invoke("config", "set", "defaults.output", "json").code == 0
        invoke("config", "set", "defaults.timeout", "12.5")
        invoke("config", "set", "accounts.work.trusted_srv_targets", '["h1", "h2"]')
        invoke("config", "set", "accounts.work.insecure", "true")
        config = load_config(config_path(environ))
        assert config.defaults.output == "json"
        assert config.defaults.timeout == 12.5
        assert config.accounts["work"].trusted_srv_targets == ["h1", "h2"]
        assert config.accounts["work"].insecure is True

    def test_an_unknown_path(self, invoke):
        result = invoke("config", "set", "accounts.nobody.url", "x")
        assert result.code == 2
        assert "is not a setting" in result.err

    def test_a_bad_value(self, invoke):
        result = invoke("config", "set", "defaults.output", "xml")
        assert result.code == 10
        assert "defaults.output" in result.err

    def test_an_unknown_key(self, invoke):
        assert invoke("config", "set", "defaults.colour", "red").code == 10
