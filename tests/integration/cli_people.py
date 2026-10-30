"""One person's ``jmap``, run in process against the server ``JMAP_TEST_URL`` names.

Shared by the live command tests. Each person gets an empty configuration
directory, adds the account with ``--password-stdin --no-keyring``, and runs
commands with ``--json``.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.app import run
from jmap.cli.context import CLIContext
from jmap.cli.secrets import MemoryStore
from tests.integration.conftest import CA_BUNDLE, INSECURE, JMAP_URL

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from jmap.client import JMAPClient

ALICE = os.environ.get("JMAP_TEST_USER", "")
ALICE_PASSWORD = os.environ.get("JMAP_TEST_PASS", "")
BOB = os.environ.get("JMAP_TEST_USER2", "")
BOB_PASSWORD = os.environ.get("JMAP_TEST_PASS2", "")

requires_server = pytest.mark.skipif(
    not (JMAP_URL and ALICE and ALICE_PASSWORD),
    reason="set JMAP_TEST_URL, JMAP_TEST_USER and JMAP_TEST_PASS to run",
)
requires_second_account = pytest.mark.skipif(
    not (BOB and BOB_PASSWORD),
    reason="set JMAP_TEST_USER2 and JMAP_TEST_PASS2 to run delivery tests",
)


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)


class Person:
    """One person's ``jmap``: an empty home directory, and no keyring.

    ``account add`` reads the password from stdin. With ``--no-keyring`` it is
    not stored, so later commands get it from ``$JMAP_PASSWORD``, as in CI.
    """

    def __init__(self, home: Path, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.environ = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
        }

    def __call__(self, *args: str) -> Result:
        environ = {**self.environ, "JMAP_PASSWORD": self.password}
        return self._run([*args, "--no-keyring"], environ, stdin="")

    def ok(self, *args: str) -> Result:
        """Run a command that must succeed."""
        return _succeeded(args, self(*args))

    def add_account(self) -> Result:
        args = ["account", "add", "live", "--url", JMAP_URL, "--username", self.username]
        args += ["--password-stdin", "--no-keyring"]
        if CA_BUNDLE:
            args += ["--ca-bundle", CA_BUNDLE]
        if INSECURE:
            args.append("--insecure")
        return _succeeded(args, self._run(args, self.environ, stdin=f"{self.password}\n"))

    def _run(self, args: list[str], environ: dict[str, str], stdin: str) -> Result:
        context = CLIContext(
            environ=environ,
            stdin=io.StringIO(stdin),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            secrets=MemoryStore(),
            width=200,
        )
        code = run(args, context=context)
        return Result(code, _text(context.out.file), _text(context.err.file))


def requires_method(client: JMAPClient, *methods: str) -> None:
    for method in methods:
        if not client.capabilities.supports(method):
            pytest.skip(f"server does not implement {method}")


def _succeeded(args: Sequence[str], result: Result) -> Result:
    assert result.code == 0, f"jmap {' '.join(args)} exited {result.code}: {result.err}"
    return result


def _text(stream: Any) -> str:
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()
