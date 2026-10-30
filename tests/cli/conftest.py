"""Fixtures for driving the real command tree against the fake server.

``invoke`` builds a :class:`CLIContext` around in-memory streams, a scripted
prompter, an in-memory secret store and an HTTP client wired to the fake
server, then runs one command line through :func:`jmap.cli.app.run` - the same
entry the installed script uses, error reporting included.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.app import run
from jmap.cli.config import AccountConfig, Config, save_config
from jmap.cli.connect import http_client
from jmap.cli.context import CLIContext
from jmap.cli.paths import config_path
from jmap.cli.secrets import MemoryStore
from jmap.testing import FakeJMAPServer
from tests.fake.mailstore import MailStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CORE_URN = "urn:ietf:params:jmap:core"
MAIL_URN = "urn:ietf:params:jmap:mail"
URL = "https://jmap.example.com/.well-known/jmap"

MAILBOXES: list[dict[str, Any]] = [
    {
        "id": "m1",
        "name": "Inbox",
        "parentId": None,
        "role": "inbox",
        "sortOrder": 1,
        "totalEmails": 3,
        "unreadEmails": 1,
        "isSubscribed": True,
        "myRights": {"mayReadItems": True, "mayDelete": False},
    },
    {
        "id": "m2",
        "name": "Archive",
        "parentId": None,
        "role": "archive",
        "sortOrder": 2,
        "totalEmails": 0,
        "unreadEmails": 0,
        "isSubscribed": True,
    },
    {
        "id": "m3",
        "name": "2025",
        "parentId": "m2",
        "role": None,
        "sortOrder": 0,
        "totalEmails": 9,
        "unreadEmails": 0,
        "isSubscribed": False,
    },
]


def mailbox_get(args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
    ids = args.get("ids")
    found = [box for box in MAILBOXES if ids is None or box["id"] in ids]
    missing = [mailbox_id for mailbox_id in ids or [] if mailbox_id not in {b["id"] for b in found}]
    return {"accountId": args["accountId"], "state": "s1", "list": found, "notFound": missing}


@pytest.fixture
def server() -> FakeJMAPServer:
    fake = FakeJMAPServer(
        capabilities={CORE_URN: {"maxCallsInRequest": 16}, MAIL_URN: {}},
        primary_accounts={CORE_URN: "a", MAIL_URN: "a"},
    )
    fake.handle("Mailbox/get", mailbox_get)
    return fake


@pytest.fixture
def store(server: FakeJMAPServer) -> MailStore:
    """A mail store answering the mail methods, in place of the fixed mailboxes."""
    return MailStore(server)


@pytest.fixture
def environ(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }


@pytest.fixture
def secrets() -> MemoryStore:
    return MemoryStore()


@dataclass
class ScriptedPrompter:
    """Answers questions from lists, and records every question asked."""

    interactive: bool = True
    answers: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    confirms: list[bool] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)

    def ask(self, prompt: str, *, default: str = "") -> str:
        self.asked.append(prompt)
        return self.answers.pop(0) if self.answers else default

    def secret(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.secrets.pop(0)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        self.asked.append(prompt)
        return self.confirms.pop(0) if self.confirms else default


@dataclass
class Result:
    code: int
    out: str
    err: str
    context: CLIContext

    def json(self) -> Any:
        return json.loads(self.out)


@pytest.fixture
def make_context(
    server: FakeJMAPServer, environ: dict[str, str], secrets: MemoryStore
) -> Callable[..., CLIContext]:
    def make(
        *,
        stdin: str = "",
        env: dict[str, str] | None = None,
        terminal: bool = False,
        prompter: ScriptedPrompter | None = None,
    ) -> CLIContext:
        return CLIContext(
            environ={**environ, **(env or {})},
            stdin=io.StringIO(stdin),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            secrets=secrets,
            prompter=prompter or ScriptedPrompter(interactive=False),
            http_factory=lambda account, verbose, err: http_client(
                account, verbose=verbose, err=err, transport=server.transport()
            ),
            terminal=terminal,
            width=200,
        )

    return make


@pytest.fixture
def invoke(make_context: Callable[..., CLIContext]) -> Callable[..., Result]:
    def go(*args: str, **kwargs: Any) -> Result:
        context = make_context(**kwargs)
        code = run(list(args), context=context)
        return Result(code, text_of(context.out.file), text_of(context.err.file), context)

    return go


def text_of(stream: Any) -> str:
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


@pytest.fixture
def account(environ: dict[str, str], secrets: MemoryStore) -> str:
    """A configured account ``work`` with its password in the store."""
    config = Config(
        default_account="work",
        accounts={"work": AccountConfig(url=URL, username="alice@example.com")},
    )
    save_config(config, config_path(environ))
    secrets.set("work", "password", "pw")
    return "work"
