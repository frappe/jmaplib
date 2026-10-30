"""``jmap push``: subscriptions, their keys, and reading what is pushed."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from jmap.push import PushKeyPair
from tests.fake.servertools import VAPID_URN, PushStore
from tests.unit.test_webpush import encrypt

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

URL = "https://push.example.com/hook/abc"
CHANGE = {"@type": "StateChange", "changed": {"a": {"Email": "s9"}}}


@pytest.fixture
def push(server: FakeJMAPServer, account: str) -> PushStore:
    store = PushStore(server)
    store.subscriptions["P0"] = {
        "id": "P0",
        "deviceClientId": "phone",
        "url": URL,
        "types": ["Email"],
        "expires": "2026-11-01T00:00:00Z",
        "verificationCode": "abc",
    }
    return store


@pytest.fixture
def no_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *rest: None if name == "cryptography" else real(name, *rest),
    )


def test_list(invoke, push):
    push.subscriptions["P1"] = {"id": "P1", "deviceClientId": "cli", "url": URL}
    rows = [line.split("\t") for line in invoke("push", "list").out.splitlines()]
    assert rows == [
        ["P0", "phone", "Email", "2026-11-01T00:00:00Z", "yes"],
        ["P1", "cli", "all", "", "no"],
    ]


class TestAdd:
    def test_a_subscription(self, invoke, push):
        result = invoke("push", "add", "--url", URL, "--types", "Email,Mailbox")
        assert result.code == 0, result.err
        assert "jmap push verify P2 CODE" in result.err
        assert push.subscriptions["P2"]["deviceClientId"] == "jmap-cli/work"
        assert push.subscriptions["P2"]["types"] == ["Email", "Mailbox"]
        assert "keys" not in push.subscriptions["P2"]

    def test_an_expiry_from_now(self, invoke, push):
        invoke("push", "add", "--url", URL, "--expires", "+2d")
        expires = datetime.fromisoformat(push.subscriptions["P2"]["expires"])
        assert timedelta(days=1) < expires - datetime.now(UTC) <= timedelta(days=2)

    def test_an_expiry_as_a_date(self, invoke, push):
        invoke("push", "add", "--url", URL, "--expires", "2026-12-01T00:00:00Z", "--json")
        assert push.subscriptions["P2"]["expires"] == "2026-12-01T00:00:00Z"

    def test_a_bad_expiry(self, invoke, push):
        assert invoke("push", "add", "--url", URL, "--expires", "soon").code == 2

    def test_http_is_refused(self, invoke, push):
        result = invoke("push", "add", "--url", "http://push.example.com/")
        assert result.code == 2
        assert "https://" in result.err

    def test_encrypted_keeps_the_key(self, invoke, push, secrets):
        result = invoke("push", "add", "--url", URL, "--encrypt")
        assert result.code == 0, result.err
        stored = json.loads(secrets.get("work/push/P2", "push") or "")
        pair = PushKeyPair(stored["privateKey"], stored["auth"])
        assert push.subscriptions["P2"]["keys"] == pair.keys.to_wire()

    def test_encrypted_needs_the_extra(self, invoke, push, no_crypto):
        result = invoke("push", "add", "--url", URL, "--encrypt")
        assert result.code == 2
        assert "jmaplib[push]" in result.err
        assert set(push.subscriptions) == {"P0"}

    def test_refused(self, invoke, push, secrets):
        result = invoke("push", "add", "--url", "https://forbidden.example/", "--encrypt")
        assert result.code == 8
        assert secrets.values == {("work", "password"): "pw"}


def test_verify(invoke, push):
    assert "verified P0" in invoke("push", "verify", "P0", "xyz").err
    assert push.subscriptions["P0"]["verificationCode"] == "xyz"


def test_renew(invoke, push):
    assert "renewed P0" in invoke("push", "renew", "P0").err
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", push.subscriptions["P0"]["expires"])


def test_remove_forgets_the_key(invoke, push, secrets):
    secrets.set("work/push/P0", "push", "{}")
    assert "removed P0" in invoke("push", "remove", "P0").err
    assert "P0" not in push.subscriptions
    assert secrets.get("work/push/P0", "push") is None


def test_remove_an_unknown_subscription(invoke, push, secrets):
    secrets.set("work/push/P9", "push", "{}")
    result = invoke("push", "remove", "P9")
    assert result.code == 8
    assert "P9: notFound" in result.err
    assert secrets.get("work/push/P9", "push") == "{}"


class TestKeys:
    def test_the_vapid_key(self, invoke, server, account):
        server.capabilities[VAPID_URN] = {"applicationServerKey": "BKey"}
        assert invoke("push", "keys").out == "BKey\n"
        assert invoke("push", "keys", "--json").json() == {"applicationServerKey": "BKey"}

    def test_no_vapid(self, invoke, account):
        assert invoke("push", "keys").code == 7


class TestDecode:
    def test_plain_from_stdin(self, invoke, account):
        result = invoke("push", "decode", stdin=json.dumps(CHANGE))
        assert result.json() == CHANGE

    def test_encrypted_from_a_file(self, invoke, account, secrets, tmp_path):
        pair = PushKeyPair.generate()
        keys = {"privateKey": pair.private_key, "auth": pair.auth}
        secrets.set("work/push/P0", "push", json.dumps(keys))
        body = tmp_path / "body"
        body.write_bytes(encrypt(json.dumps(CHANGE).encode(), pair.keys))
        result = invoke("push", "decode", str(body), "--subscription", "P0")
        assert result.json() == CHANGE

    def test_no_kept_key(self, invoke, account):
        result = invoke("push", "decode", "--subscription", "P9", stdin="{}")
        assert result.code == 9

    def test_decrypting_needs_the_extra(self, invoke, account, secrets, no_crypto):
        secrets.set("work/push/P0", "push", "{}")
        assert invoke("push", "decode", "--subscription", "P0", stdin="{}").code == 2

    def test_not_a_push(self, invoke, account):
        result = invoke("push", "decode", stdin='{"@type": "Email"}')
        assert result.code == 1
        assert "unexpected @type" in result.err
