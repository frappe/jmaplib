"""``jmap sieve``: list, get, put, validate, activate, deactivate and delete."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.servertools import SieveStore

from .conftest import ScriptedPrompter

if TYPE_CHECKING:
    from pathlib import Path

    from jmap.testing import FakeJMAPServer

SCRIPT = 'require ["fileinto"];\nfileinto "Lists";\n'


@pytest.fixture
def sieve(server: FakeJMAPServer, account: str) -> SieveStore:
    store = SieveStore(server)
    store.add("vacation", "keep;", active=True)
    store.add("lists", SCRIPT)
    return store


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "filters.sieve"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def test_list(invoke, sieve):
    rows = [line.split("\t") for line in invoke("sieve", "list").out.splitlines()]
    assert [(row[1], row[2]) for row in rows] == [("vacation", "yes"), ("lists", "")]


class TestGet:
    def test_by_name(self, invoke, sieve):
        assert invoke("sieve", "get", "lists").out == SCRIPT

    def test_by_id_to_a_file(self, invoke, sieve, tmp_path):
        path = tmp_path / "copy.sieve"
        result = invoke("sieve", "get", "s1", "--save", str(path))
        assert path.read_text(encoding="utf-8") == "keep;"
        assert "saved" in result.err

    def test_an_unknown_script(self, invoke, sieve):
        result = invoke("sieve", "get", "nope")
        assert result.code == 9
        assert "jmap sieve list" in result.err


class TestPut:
    def test_a_new_script_named_after_its_file(self, invoke, sieve, script):
        result = invoke("sieve", "put", str(script))
        assert result.code == 0, result.err
        assert "stored the script filters" in result.err
        [new] = [item for item in sieve.scripts.values() if item["name"] == "filters"]
        assert sieve.text(new["id"]) == SCRIPT
        assert not new["isActive"]

    def test_replacing_one_and_activating_it(self, invoke, sieve, script):
        result = invoke("sieve", "put", str(script), "--name", "vacation", "--activate")
        assert result.code == 0, result.err
        assert sieve.text("s1") == SCRIPT
        assert "vacation is the active script" in result.err

    def test_creating_and_activating_in_one_request(self, invoke, sieve, script, server):
        invoke("sieve", "put", str(script), "--activate")
        [new] = [item for item in sieve.scripts.values() if item["name"] == "filters"]
        assert new["isActive"]
        assert len(server.requests[-1]["methodCalls"]) == 2

    def test_from_stdin_needs_a_name(self, invoke, sieve):
        assert invoke("sieve", "put", "-", stdin=SCRIPT).code == 2
        assert invoke("sieve", "put", "-", "--name", "piped", stdin=SCRIPT).code == 0

    def test_an_invalid_script_is_not_stored(self, invoke, sieve):
        result = invoke("sieve", "put", "-", "--name", "bad", stdin="error here")
        assert result.code == 2
        assert "line 1: unknown command" in result.err
        assert len(sieve.scripts) == 2

    def test_without_validating(self, invoke, sieve, server):
        result = invoke("sieve", "put", "-", "--name", "bad", "--no-validate", stdin="error")
        assert result.code == 0
        calls = [call[0] for request in server.requests for call in request["methodCalls"]]
        assert "SieveScript/validate" not in calls


class TestValidate:
    def test_valid(self, invoke, sieve, script):
        result = invoke("sieve", "validate", str(script))
        assert result.code == 0
        assert "valid" in result.err

    def test_invalid_as_json(self, invoke, sieve):
        result = invoke("sieve", "validate", "-", "--json", stdin="error")
        assert result.code == 2
        assert result.json()["error"]["type"] == "invalidSieve"


def test_activate(invoke, sieve):
    result = invoke("sieve", "activate", "lists")
    assert "lists is the active script" in result.err
    assert sieve.scripts["s2"]["isActive"]
    assert not sieve.scripts["s1"]["isActive"]


def test_deactivate(invoke, sieve):
    assert "no script is active" in invoke("sieve", "deactivate").err
    assert not any(item["isActive"] for item in sieve.scripts.values())


class TestDelete:
    def test_the_active_script_is_deactivated_first(self, invoke, sieve, server):
        result = invoke("sieve", "delete", "vacation", "--yes")
        assert result.code == 0, result.err
        assert "s1" not in sieve.scripts
        calls = server.requests[-1]["methodCalls"]
        assert calls[0][1] == {"accountId": "a", "onSuccessDeactivateScript": True}

    def test_an_inactive_script_after_confirming(self, invoke, sieve):
        prompter = ScriptedPrompter(confirms=[True])
        assert invoke("sieve", "delete", "lists", prompter=prompter).code == 0
        assert "s2" not in sieve.scripts

    def test_needs_yes_off_a_terminal(self, invoke, sieve):
        assert invoke("sieve", "delete", "lists").code == 1
        assert "s2" in sieve.scripts


def test_a_server_without_sieve(invoke, account):
    result = invoke("sieve", "list")
    assert result.code == 7
    assert "urn:ietf:params:jmap:sieve" in result.err
