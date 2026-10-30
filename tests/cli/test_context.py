"""The per-run context: configuration, connection, capability gating, confirmation."""

from __future__ import annotations

import logging
import os
import sys

import httpx
import pytest

from jmap.cli.config import ResolvedAccount
from jmap.cli.context import CLIContext, GlobalFlags
from jmap.cli.errors import CLIError, UnsupportedError
from jmap.cli.output import OutputFormat
from jmap.cli.prompts import TerminalPrompter
from jmap.cli.secrets import KeyringStore, MemoryStore
from jmap.core.errors import TransportError

from .conftest import CORE_URN, MAIL_URN, ScriptedPrompter

CALENDARS_URN = "urn:ietf:params:jmap:calendars"


def test_from_environment_uses_the_real_streams():
    context = CLIContext.from_environment({})
    assert context.stdin is sys.stdin
    assert isinstance(context.secrets, KeyringStore)
    assert isinstance(context.prompter, TerminalPrompter)


def test_the_default_http_client_carries_the_account_settings():
    context = CLIContext.from_environment({})
    account = ResolvedAccount(
        name="w", address="", url="u", auth="password", username="u", account_id="", timeout=7
    )
    with context.http_factory(account, 0, context.err) as http:
        assert http.timeout.read == 7


class TestConfigure:
    def test_colour(self, make_context):
        context = make_context()
        context.configure(GlobalFlags(color="always"))
        assert context.out.is_terminal
        assert not context.out.no_color
        context.configure(GlobalFlags(color="never"))
        assert context.err.no_color
        context = make_context(env={"NO_COLOR": "1"})
        context.configure(GlobalFlags())
        assert context.out.no_color

    def test_stderr_lines_never_wrap(self, make_context):
        context = make_context()
        context.configure(GlobalFlags())
        context.err.print("x" * 500)
        assert context.err.file.getvalue() == "x" * 500 + "\n"

    def test_no_keyring_keeps_secrets_in_memory(self, make_context):
        context = make_context()
        context.secrets.set("x", "password", "kept")
        context.configure(GlobalFlags(no_keyring=True))
        assert isinstance(context.secrets, MemoryStore)
        assert context.secrets.get("x", "password") is None

    def test_output_comes_from_the_flag_then_the_file(self, make_context, environ, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[defaults]\noutput = "jsonl"\npager = "never"\n')
        context = make_context()
        context.configure(GlobalFlags(config=str(path)))
        assert context.options.format is OutputFormat.JSONL
        assert context.options.pager == ""
        flagged = make_context()
        flagged.configure(GlobalFlags(config=str(path), output=OutputFormat.IDS))
        assert flagged.options.format is OutputFormat.IDS

    def test_pager_from_the_environment(self, make_context):
        context = make_context(env={"PAGER": "most", "JMAP_PAGER": "bat"})
        context.configure(GlobalFlags())
        assert context.options.pager == "bat"

    def test_pager_fallback(self, make_context, monkeypatch):
        context = make_context()
        context.configure(GlobalFlags())
        assert context.options.pager == ("more" if os.name == "nt" else "less -FRX")

    def test_a_broken_config_is_reported_later_not_here(self, make_context, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text("= broken")
        context = make_context()
        context.configure(GlobalFlags(config=str(path)))
        assert context.options.format is OutputFormat.AUTO
        with pytest.raises(CLIError):
            _ = context.config

    def test_very_verbose_logs_the_wire_until_closed(self, make_context):
        context = make_context()
        context.configure(GlobalFlags(verbose=2))
        logger = logging.getLogger("httpx")
        assert logger.level == logging.DEBUG
        assert context._log_handler in logger.handlers
        handler = context._log_handler
        context.close()
        assert handler not in logger.handlers
        context.close()


class TestConnection:
    @pytest.mark.usefixtures("account")
    def test_the_client_is_opened_once(self, make_context):
        context = make_context()
        assert context.client() is context.client()
        assert context.mailboxes() is context.mailboxes()

    @pytest.mark.usefixtures("account")
    def test_a_failed_connection_closes_its_http_client(self, make_context, server):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        server.intercept = refuse
        context = make_context()
        with pytest.raises(TransportError):
            context.client()
        assert context._http is None

    @pytest.mark.usefixtures("account")
    def test_use_account(self, make_context):
        context = make_context()
        context.use_account(None)
        assert context.account.name == "work"
        context.client()
        context.use_account("work")
        assert context._client is None


class TestRequire:
    @pytest.mark.usefixtures("account")
    def test_supported(self, make_context):
        make_context().require("Mailbox/get")

    @pytest.mark.usefixtures("account")
    def test_not_advertised(self, make_context):
        with pytest.raises(UnsupportedError, match="does not advertise urn:ietf:params:jmap:sieve"):
            make_context().require("SieveScript/get")

    @pytest.mark.usefixtures("account")
    def test_unknown(self, make_context):
        with pytest.raises(UnsupportedError, match="does not offer Nope/get"):
            make_context().require("Nope/get")

    @pytest.mark.usefixtures("account")
    def test_asking_for_an_experimental_method_opts_in(self, make_context, server):
        server.capabilities[CALENDARS_URN] = {}
        server.accounts["a"]["accountCapabilities"] = {CALENDARS_URN: {}, MAIL_URN: {}}
        server.primary_accounts[CALENDARS_URN] = "a"
        context = make_context()
        client = context.require("Calendar/get")
        assert client.experimental
        assert client.capabilities.supports("Calendar/get")

    @pytest.mark.usefixtures("account")
    def test_opting_in_does_not_help_an_unadvertised_one(self, make_context):
        with pytest.raises(UnsupportedError, match="does not advertise"):
            make_context().require("Calendar/get")


class TestConfirm:
    def test_yes(self, make_context):
        context = make_context()
        context.configure(GlobalFlags(yes=True))
        context.confirm("Delete?")

    def test_without_a_terminal(self, make_context):
        with pytest.raises(CLIError, match="refusing without a terminal"):
            make_context().confirm("Delete?")

    def test_declined_and_accepted(self, make_context):
        with pytest.raises(CLIError, match="cancelled"):
            make_context(prompter=ScriptedPrompter(confirms=[False])).confirm("Delete?")
        make_context(prompter=ScriptedPrompter(confirms=[True])).confirm("Delete?")


def test_core_only_server_is_fine(make_context, account, server):
    server.capabilities = {CORE_URN: {}}
    assert make_context().client().capabilities.supports("Core/echo")
