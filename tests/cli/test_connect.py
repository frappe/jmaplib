"""The HTTP client, -v tracing, and opening a client for one named account."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import certifi
import httpx
import pytest
from rich.console import Console

import jmap.discovery
from jmap.auth import BasicAuth
from jmap.cli.config import ResolvedAccount
from jmap.cli.connect import _Tracer, find_account, http_client, open_client
from jmap.cli.errors import NotFoundError
from jmap.testing import FakeJMAPServer

from .conftest import CORE_URN, MAIL_URN, URL, ScriptedPrompter

if TYPE_CHECKING:
    from jmap.core.session import Session

ACCOUNT = ResolvedAccount(
    name="work", address="", url=URL, auth="password", username="alice", account_id=""
)
AUTH = BasicAuth("alice", "pw")


def console() -> tuple[Console, io.StringIO]:
    stream = io.StringIO()
    return Console(file=stream, width=200), stream


class TestHttpClient:
    def test_insecure_warns_once(self):
        err, stream = console()
        http_client(replace(ACCOUNT, insecure=True), verbose=0, err=err).close()
        assert "not being verified" in stream.getvalue()

    def test_a_ca_bundle(self):
        err, stream = console()
        http_client(replace(ACCOUNT, ca_bundle=certifi.where()), verbose=0, err=err).close()
        assert stream.getvalue() == ""

    def test_verbose_traces_requests_without_credentials(self, server):
        err, stream = console()
        http = http_client(ACCOUNT, verbose=1, err=err, transport=server.transport())
        client = open_client(ACCOUNT, auth=AUTH, http=http, prompter=ScriptedPrompter())
        client.echo(x=1)
        lines = stream.getvalue().splitlines()
        assert lines[0] == f"-> GET {URL}"
        assert lines[1].startswith("<- 307 (")
        assert "-> POST https://jmap.example.com/jmap/ [Core/echo]" in lines
        assert "Basic" not in stream.getvalue()


class TestTracer:
    def lines(self, request: httpx.Request) -> str:
        err, stream = console()
        _Tracer(err).request(request)
        return stream.getvalue()

    def test_bodies_that_are_not_method_calls(self):
        headers = {"content-type": "application/json"}
        url = "https://x/api"
        assert self.lines(httpx.Request("POST", url, headers=headers, content=b"{")).endswith(
            "/api\n"
        )
        for body in ([1], {"methodCalls": 3}):
            request = httpx.Request("POST", url, headers=headers, content=json.dumps(body))
            assert "[" not in self.lines(request)

    def test_a_response_without_a_start_time(self):
        err, stream = console()
        request = httpx.Request("GET", "https://x")
        _Tracer(err).response(httpx.Response(200, request=request))
        assert stream.getvalue().startswith("<- 200 (")


class TestOpenClient:
    def http(self, server: FakeJMAPServer) -> httpx.Client:
        return httpx.Client(**server.client_kwargs())

    def test_by_url(self, server):
        client = open_client(
            ACCOUNT, auth=AUTH, http=self.http(server), prompter=ScriptedPrompter()
        )
        assert client.default_account == "a"

    def test_by_address(self, server, monkeypatch):
        monkeypatch.setattr(jmap.discovery, "lookup_srv", lambda domain, **kw: [])
        account = replace(ACCOUNT, url="", address="alice@jmap.example.com")
        client = open_client(
            account, auth=AUTH, http=self.http(server), prompter=ScriptedPrompter()
        )
        assert client.session.username == "alice@example.com"

    def test_selecting_a_jmap_account_by_name(self):
        server = FakeJMAPServer(
            capabilities={CORE_URN: {}, MAIL_URN: {}},
            accounts={
                "a": {"name": "Alice", "accountCapabilities": {MAIL_URN: {}}},
                "s": {"name": "Sales", "isReadOnly": True, "accountCapabilities": {MAIL_URN: {}}},
            },
            primary_accounts={CORE_URN: "a", MAIL_URN: "a"},
        )
        account = replace(ACCOUNT, account_id="sales")
        client = open_client(
            account, auth=AUTH, http=self.http(server), prompter=ScriptedPrompter()
        )
        assert client.default_account == "s"
        assert client.capabilities.account_id == "s"


class TestFindAccount:
    def session(self, **accounts: str) -> Session:
        server = FakeJMAPServer(accounts={key: {"name": name} for key, name in accounts.items()})
        http = httpx.Client(**server.client_kwargs())
        return open_client(ACCOUNT, auth=AUTH, http=http, prompter=ScriptedPrompter()).session

    def test_by_id(self):
        assert find_account(self.session(a="Alice"), "a") == "a"

    def test_missing(self):
        with pytest.raises(
            NotFoundError, match=r"no JMAP account 'x'; this session has: a \(Alice\)"
        ):
            find_account(self.session(a="Alice"), "x")

    def test_ambiguous(self):
        with pytest.raises(NotFoundError, match="several JMAP accounts are named 'TEAM'"):
            find_account(self.session(a="Team", b="team"), "TEAM")
