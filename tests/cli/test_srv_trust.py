"""SRV targets outside the address's domain: asked about once, then remembered."""

from __future__ import annotations

import pytest

import jmap.discovery
from jmap.cli.config import AccountConfig, Config, load_config, save_config
from jmap.cli.paths import config_path
from jmap.discovery import SRVTarget

from .conftest import ScriptedPrompter


@pytest.fixture(autouse=True)
def srv(monkeypatch):
    # DNS says alice@other.org is served by jmap.example.com, outside other.org.
    target = SRVTarget(host="jmap.example.com", port=443)
    monkeypatch.setattr(jmap.discovery, "lookup_srv", lambda domain, **kw: [target])


def test_add_saves_the_host_the_person_agreed_to(invoke, environ):
    prompter = ScriptedPrompter(confirms=[True])
    result = invoke(
        "account",
        "add",
        "home",
        "--address",
        "alice@other.org",
        "--password-stdin",
        stdin="pw",
        prompter=prompter,
    )
    assert result.code == 0, result.err
    assert "jmap.example.com:443" in prompter.asked[0]
    stored = load_config(config_path(environ)).accounts["home"]
    assert stored.trusted_srv_targets == ["jmap.example.com"]


def test_an_existing_account_remembers_it_too(invoke, environ, secrets):
    path = config_path(environ)
    save_config(Config(accounts={"home": AccountConfig(address="alice@other.org")}), path)
    secrets.set("home", "password", "pw")
    assert invoke("server", "echo", prompter=ScriptedPrompter(confirms=[True])).code == 0
    assert load_config(path).accounts["home"].trusted_srv_targets == ["jmap.example.com"]
    # Asked once: the next run trusts it without a question.
    quiet = ScriptedPrompter(interactive=False)
    assert invoke("server", "echo", prompter=quiet).code == 0
    assert quiet.asked == []


def test_declining_is_not_remembered(invoke, environ, secrets):
    path = config_path(environ)
    save_config(Config(accounts={"home": AccountConfig(address="alice@other.org")}), path)
    secrets.set("home", "password", "pw")
    declined = ScriptedPrompter(confirms=[False])
    # Discovery moves on to https://other.org/.well-known/jmap, which the fake answers.
    assert invoke("server", "echo", prompter=declined).code == 0
    assert len(declined.asked) == 1
    assert load_config(path).accounts["home"].trusted_srv_targets == []
