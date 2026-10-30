"""Where a secret comes from, in order, and the credential built from it."""

from __future__ import annotations

import pytest

from jmap.auth import BasicAuth, BearerAuth
from jmap.cli.auth import Credentials, make_auth
from jmap.cli.config import ResolvedAccount
from jmap.cli.errors import AuthError
from jmap.cli.secrets import KeyringUnavailableError, MemoryStore

from .conftest import ScriptedPrompter

ACCOUNT = ResolvedAccount(
    name="work", address="", url="u", auth="password", username="alice", account_id=""
)


class Unavailable(MemoryStore):
    def get(self, account, kind):
        raise KeyringUnavailableError("no usable keyring (none)")


def credentials(
    *,
    secrets: MemoryStore | None = None,
    environ: dict[str, str] | None = None,
    prompter: ScriptedPrompter | None = None,
    stdin_secret: str | None = None,
    warnings: list[str] | None = None,
) -> Credentials:
    return Credentials(
        secrets=secrets or MemoryStore(),
        environ=environ or {},
        prompter=prompter or ScriptedPrompter(interactive=False),
        warn=(warnings if warnings is not None else []).append,
        stdin_secret=stdin_secret,
    )


class TestOrder:
    def test_stdin_first(self):
        found = credentials(stdin_secret="s", environ={"JMAP_PASSWORD": "e"})
        assert (found.secret(ACCOUNT), found.source) == ("s", "stdin")

    def test_then_the_environment(self):
        store = MemoryStore()
        store.set("work", "password", "k")
        found = credentials(secrets=store, environ={"JMAP_PASSWORD": "e"})
        assert (found.secret(ACCOUNT), found.source) == ("e", "environment")

    def test_then_the_keyring(self):
        store = MemoryStore()
        store.set("work", "password", "k")
        found = credentials(secrets=store)
        assert (found.secret(ACCOUNT), found.source) == ("k", "keyring")

    def test_then_a_prompt(self):
        prompter = ScriptedPrompter(secrets=["p"])
        found = credentials(prompter=prompter)
        assert (found.secret(ACCOUNT), found.source) == ("p", "prompt")
        assert prompter.asked == ["Password for work"]

    def test_otherwise_an_error_naming_the_remedies(self):
        with pytest.raises(AuthError, match="no password for account 'work'") as caught:
            credentials().secret(ACCOUNT)
        hint = caught.value.hint or ""
        assert "jmap account login work" in hint
        assert "JMAP_PASSWORD" in hint
        assert "--password-stdin" in hint

    def test_a_token_reads_its_own_variable(self):
        token = ResolvedAccount(
            name="t", address="", url="u", auth="token", username="", account_id=""
        )
        assert credentials(environ={"JMAP_TOKEN": "tk"}).secret(token) == "tk"

    def test_an_environment_account_has_nothing_in_the_keyring(self):
        store = MemoryStore()
        store.set("env", "password", "stale")
        env = ResolvedAccount(
            name="env",
            address="",
            url="u",
            auth="password",
            username="u",
            account_id="",
            synthetic=True,
        )
        with pytest.raises(AuthError):
            credentials(secrets=store).secret(env)

    def test_no_keyring_warns_and_carries_on(self):
        warnings: list[str] = []
        found = credentials(
            secrets=Unavailable(),
            environ={},
            prompter=ScriptedPrompter(secrets=["p"]),
            warnings=warnings,
        )
        assert found.secret(ACCOUNT) == "p"
        assert warnings == ["no usable keyring (none); trying the other sources"]


class TestMakeAuth:
    def test_password_is_basic(self):
        assert isinstance(credentials(stdin_secret="s").auth(ACCOUNT), BasicAuth)

    def test_token_is_bearer(self):
        token = ResolvedAccount(
            name="t", address="", url="u", auth="token", username="", account_id=""
        )
        assert isinstance(make_auth(token, "tk"), BearerAuth)

    def test_a_password_needs_a_username(self):
        nameless = ResolvedAccount(
            name="n", address="", url="u", auth="password", username="", account_id=""
        )
        with pytest.raises(AuthError, match="no username"):
            make_auth(nameless, "pw")
