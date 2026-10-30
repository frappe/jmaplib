"""``jmap account``: adding, listing, showing, removing and logging in."""

from __future__ import annotations

import httpx
import pytest

import jmap.discovery
from jmap.cli.app import run
from jmap.cli.config import AccountConfig, Config, load_config, save_config
from jmap.cli.paths import config_path
from jmap.cli.secrets import KeyringUnavailableError, MemoryStore

from .conftest import CORE_URN, MAIL_URN, URL, ScriptedPrompter

ADD = ("account", "add", "work", "--url", URL, "--username", "alice@example.com")


def stored(environ: dict[str, str]) -> Config:
    return load_config(config_path(environ))


class TestAdd:
    def test_from_stdin(self, invoke, environ, secrets):
        result = invoke(*ADD, "--password-stdin", stdin="pw\n")
        assert result.code == 0, result.err
        assert "alice@example.com" in result.out
        assert f"added 'work' to {config_path(environ)}" in result.err
        config = stored(environ)
        assert config.default_account == "work"
        assert config.accounts["work"].url == URL
        assert secrets.get("work", "password") == "pw"

    def test_global_flags_are_stored_with_it(self, invoke, environ):
        result = invoke(*ADD, "--account-id", "a", "--experimental", "--password-stdin", stdin="pw")
        assert result.code == 0, result.err
        account = stored(environ).accounts["work"]
        assert account.account_id == "a"
        assert account.experimental

    def test_a_token(self, invoke, secrets):
        result = invoke("account", "add", "t", "--url", URL, "--token-stdin", stdin="tk\n")
        assert result.code == 0, result.err
        assert secrets.get("t", "token") == "tk"

    def test_from_a_prompt(self, invoke, secrets):
        prompter = ScriptedPrompter(secrets=["typed"])
        assert invoke(*ADD, prompter=prompter).code == 0
        assert secrets.get("work", "password") == "typed"

    def test_from_the_environment(self, invoke, secrets):
        assert invoke(*ADD, env={"JMAP_PASSWORD": "e"}).code == 0
        assert secrets.get("work", "password") == "e"

    def test_by_address_without_checking(self, invoke, environ):
        result = invoke(
            "account",
            "add",
            "home",
            "--address",
            "a@example.com",
            "--trust-srv",
            "h",
            "--no-verify",
            "--password-stdin",
            stdin="pw",
        )
        assert result.code == 0, result.err
        assert stored(environ).accounts["home"].trusted_srv_targets == ["h"]

    def test_by_address_with_discovery(self, invoke, monkeypatch):
        monkeypatch.setattr(jmap.discovery, "lookup_srv", lambda domain, **kw: [])
        result = invoke(
            "account",
            "add",
            "home",
            "--address",
            "alice@jmap.example.com",
            "--password-stdin",
            stdin="pw",
        )
        assert result.code == 0, result.err

    def test_a_second_account_keeps_the_default_unless_asked(self, invoke, environ):
        invoke(*ADD, "--password-stdin", stdin="pw")
        invoke(
            "account",
            "add",
            "b",
            "--url",
            URL,
            "--username",
            "b",
            "--no-verify",
            "--password-stdin",
            stdin="pw",
        )
        assert stored(environ).default_account == "work"
        invoke(
            "account",
            "add",
            "c",
            "--url",
            URL,
            "--username",
            "c",
            "--no-verify",
            "--default",
            "--password-stdin",
            stdin="pw",
        )
        assert stored(environ).default_account == "c"

    def test_an_existing_name_needs_force(self, invoke):
        invoke(*ADD, "--password-stdin", stdin="pw")
        refused = invoke(*ADD, "--password-stdin", stdin="pw")
        assert refused.code == 2
        assert "pass --force" in refused.err
        assert invoke(*ADD, "--force", "--password-stdin", stdin="pw").code == 0

    def test_without_keyring(self, invoke, secrets):
        result = invoke(*ADD, "--no-keyring", "--password-stdin", stdin="pw")
        assert result.code == 0
        assert "not stored (--no-keyring)" in result.err
        assert secrets.get("work", "password") is None

    def test_when_the_keyring_is_unavailable(self, make_context, invoke):
        class Broken(MemoryStore):
            def set(self, account, kind, value):
                raise KeyringUnavailableError("no usable keyring (x)")

        context = make_context(stdin="pw")
        context.secrets = Broken()
        assert run([*ADD, "--password-stdin"], context=context) == 0
        assert "was not stored: no usable keyring" in context.err.file.getvalue()

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("account", "add", "x"), "give --url"),
            ((*ADD, "--password-stdin", "--token-stdin"), "not both"),
            ((*ADD, "--auth", "basic"), "--auth must be one of"),
            ((*ADD, "--auth", "oauth", "--password-stdin"), "through the browser, not stdin"),
            ((*ADD, "--auth", "token", "--password-stdin"), "uses a token"),
            ((*ADD, "--token-stdin", "--auth", "password"), "uses a password"),
            ((*ADD, "--password-stdin"), "no password on stdin"),
        ],
    )
    def test_usage_errors(self, invoke, args, message):
        result = invoke(*args)
        assert result.code == 2
        assert message in result.err

    @pytest.mark.parametrize(
        ("name", "hint"),
        [
            ("sagar@example.com", "pass --username sagar@example.com"),
            ("work", "pass --username, or --address"),
        ],
    )
    def test_no_username_fails_before_asking(self, invoke, name, hint):
        prompter = ScriptedPrompter(secrets=["never read"])
        result = invoke("account", "add", name, "--url", URL, prompter=prompter)
        assert result.code == 2
        assert "has no username to sign in with" in result.err
        assert hint in result.err
        assert prompter.asked == []

    def test_a_bad_password_is_not_stored(self, invoke, server, secrets):
        server.intercept = lambda request: httpx.Response(401)
        result = invoke(*ADD, "--password-stdin", stdin="wrong")
        assert result.code == 3
        assert secrets.get("work", "password") is None


class TestList:
    def test_empty(self, invoke):
        assert invoke("account", "list").out == ""

    @pytest.mark.usefixtures("account")
    def test_plain_marks_the_default(self, invoke):
        assert invoke("account", "list").out == f"*\twork\talice@example.com\t{URL}\tpassword\n"

    def test_discovered_accounts(self, invoke, environ):
        save_config(
            Config(accounts={"home": AccountConfig(address="a@x.org")}), config_path(environ)
        )
        assert invoke("account", "list").out == "\thome\ta@x.org\t(discovered)\tpassword\n"

    @pytest.mark.usefixtures("account")
    def test_json(self, invoke):
        [item] = invoke("account", "list", "--json").json()
        assert item["name"] == "work"
        assert item["url"] == URL


class TestShow:
    @pytest.mark.usefixtures("account")
    def test_lists_the_sessions_jmap_accounts(self, invoke):
        result = invoke("account", "show")
        assert result.out == "*\ta\talice@example.com\tyes\tno\tcore, mail\n"
        assert f"alice@example.com at {URL}" in result.err

    @pytest.mark.usefixtures("account")
    def test_json_is_the_session_account_with_its_id(self, invoke):
        [item] = invoke("account", "show", "work", "--json").json()
        assert item == {
            "id": "a",
            "name": "alice@example.com",
            "isPersonal": True,
            "isReadOnly": False,
        }

    @pytest.mark.usefixtures("account")
    def test_shared_accounts(self, invoke, server):
        server.accounts["s"] = {
            "name": "Sales",
            "isPersonal": False,
            "isReadOnly": True,
            "accountCapabilities": {MAIL_URN: {}},
        }
        server.primary_accounts = {CORE_URN: "a"}
        lines = invoke("account", "show").out.splitlines()
        assert lines[1] == "\ts\tSales\tno\tyes\t"


class TestRemove:
    @pytest.mark.usefixtures("account")
    def test_the_default(self, invoke, environ, secrets):
        result = invoke("account", "remove", "work")
        assert result.code == 0
        assert "removed 'work'" in result.err
        assert stored(environ) == Config()
        assert secrets.get("work", "password") is None

    def test_another(self, invoke, environ, account):
        invoke(
            "account",
            "add",
            "b",
            "--url",
            URL,
            "--username",
            "b",
            "--no-verify",
            "--password-stdin",
            stdin="pw",
        )
        assert invoke("account", "remove", "b").code == 0
        assert stored(environ).default_account == "work"

    def test_missing(self, invoke):
        assert invoke("account", "remove", "x").code == 9

    @pytest.mark.usefixtures("account")
    def test_keyring_unavailable(self, make_context):
        class Broken(MemoryStore):
            def delete(self, account, kind):
                raise KeyringUnavailableError("no usable keyring (x)")

        context = make_context()
        context.secrets = Broken()
        assert run(["account", "remove", "work"], context=context) == 0
        assert "secrets not removed" in context.err.file.getvalue()


class TestSetDefault:
    def test_switches(self, invoke, environ, account):
        invoke(
            "account",
            "add",
            "b",
            "--url",
            URL,
            "--username",
            "b",
            "--no-verify",
            "--password-stdin",
            stdin="pw",
        )
        assert invoke("account", "set-default", "b").code == 0
        assert stored(environ).default_account == "b"

    def test_missing(self, invoke):
        assert invoke("account", "set-default", "x").code == 9


class TestTest:
    @pytest.mark.usefixtures("account")
    def test_ok(self, invoke, server):
        result = invoke("account", "test", "--json")
        assert result.code == 0
        assert result.json()["username"] == "alice@example.com"
        assert server.requests[-1]["methodCalls"][0][0] == "Core/echo"


class TestLogin:
    @pytest.mark.usefixtures("account")
    def test_from_stdin(self, invoke, secrets):
        result = invoke("account", "login", "--password-stdin", stdin="new\n")
        assert result.code == 0
        assert "stored the password for 'work'" in result.err
        assert secrets.get("work", "password") == "new"

    @pytest.mark.usefixtures("account")
    def test_from_a_prompt_without_checking(self, invoke, secrets, server):
        prompter = ScriptedPrompter(secrets=["typed"])
        assert invoke("account", "login", "work", "--no-verify", prompter=prompter).code == 0
        assert secrets.get("work", "password") == "typed"
        assert server.requests == []

    @pytest.mark.usefixtures("account")
    def test_ignores_the_old_secret(self, invoke):
        result = invoke("account", "login", env={"JMAP_PASSWORD": "e"})
        assert result.code == 3
        assert "--password-stdin" in result.err

    def test_not_for_environment_accounts(self, invoke):
        result = invoke("account", "login", env={"JMAP_URL": URL})
        assert result.code == 2
        assert "comes from JMAP_URL" in result.err
