"""Entry points: ``main``, ``run``, ``python -m jmap``, and moving global options."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import subprocess
import sys

import pytest
from typer.testing import CliRunner

import jmap.cli
from jmap.__about__ import __version__
from jmap.cli.app import app, hoist_globals, run
from jmap.cli.context import CLIContext


class TestMain:
    def test_without_the_extra(self, monkeypatch, capsys):
        real = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util, "find_spec", lambda name: None if name == "rich" else real(name)
        )
        assert jmap.cli.main(["--version"]) == 2
        assert "needs rich: pip install 'jmaplib[cli]'" in capsys.readouterr().err

    def test_with_it(self, capsys):
        assert jmap.cli.main(["--version"]) == 0
        assert capsys.readouterr().out == f"jmap {__version__}\n"

    def test_both_command_names(self):
        scripts = {
            point.name: point.value
            for point in importlib.metadata.entry_points(group="console_scripts")
            if point.value == "jmap.cli:main"
        }
        assert scripts == {"jmap": "jmap.cli:main", "jmap-cli": "jmap.cli:main"}

    def test_python_dash_m(self):
        done = subprocess.run(
            [sys.executable, "-m", "jmap", "--version"], capture_output=True, text=True, check=False
        )
        assert (done.returncode, done.stdout) == (0, f"jmap {__version__}\n")


class TestRun:
    def test_reads_sys_argv(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["jmap", "--version"])
        assert run() == 0
        assert __version__ in capsys.readouterr().out

    def test_no_arguments_prints_help(self, invoke, capsys):
        assert invoke().code == 2
        assert "account" in capsys.readouterr().out

    def test_a_usage_error_exits_2(self, invoke, capsys):
        assert invoke("mailbox", "frob").code == 2
        assert "No such command" in capsys.readouterr().err

    def test_help_exits_0(self, invoke, capsys):
        assert invoke("server", "--help").code == 0
        assert "conformance" in capsys.readouterr().out

    @pytest.mark.parametrize(("exit_", "code"), [(SystemExit("bye"), 1), (SystemExit(), 0)])
    def test_sys_exit_inside_a_command(self, monkeypatch, invoke, exit_, code):
        def leave(self: CLIContext, flags: object) -> None:
            raise exit_

        monkeypatch.setattr(CLIContext, "configure", leave)
        assert invoke("account", "list").code == code

    @pytest.mark.usefixtures("account")
    def test_jsonl(self, invoke):
        assert invoke("mailbox", "list", "--jsonl").out.count("\n") == 3

    def test_typer_alone_builds_its_own_context(self, environ, monkeypatch):
        for key, value in environ.items():
            monkeypatch.setenv(key, value)
        result = CliRunner().invoke(app, ["account", "list"])
        assert result.exit_code == 0
        assert result.output == ""


class TestHoist:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["mail", "list", "--json"], ["--json", "mail", "list"]),
            (["mailbox", "list", "-a", "work", "-vv"], ["-a", "work", "-vv", "mailbox", "list"]),
            (["x", "--output=json", "y"], ["--output=json", "x", "y"]),
            (["x", "-o=json"], ["x", "-o=json"]),
            (["x", "--subject=hi"], ["x", "--subject=hi"]),
            (["x", "--", "--json"], ["x", "--", "--json"]),
            (["x", "-a"], ["x", "-a"]),
            (["x", "-"], ["x", "-"]),
        ],
    )
    def test_moves_globals_to_the_front(self, argv, expected):
        assert hoist_globals(argv) == expected
