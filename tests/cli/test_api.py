"""``jmap api call``: raw arguments in, response arguments out."""

from __future__ import annotations

import pytest

from .conftest import MAILBOXES

pytestmark = pytest.mark.usefixtures("account")


def test_inline_arguments_and_the_account_filled_in(invoke, server):
    result = invoke("api", "call", "Mailbox/get", '{"ids": ["m1"]}')
    assert result.code == 0, result.err
    assert result.json()["list"] == MAILBOXES[:1]
    assert server.requests[-1]["methodCalls"][0][1] == {"ids": ["m1"], "accountId": "a"}


def test_no_arguments(invoke):
    assert len(invoke("api", "call", "Mailbox/get").json()["list"]) == 3


def test_from_a_file(invoke, tmp_path):
    path = tmp_path / "args.json"
    path.write_text('{"ids": ["m2"]}')
    assert invoke("api", "call", "Mailbox/get", f"@{path}").json()["list"] == MAILBOXES[1:2]


def test_from_stdin(invoke):
    assert invoke("api", "call", "Core/echo", "-", stdin='{"x": 1}').json() == {"x": 1}


def test_extra_using(invoke, server):
    invoke("api", "call", "Core/echo", "--using", "urn:ietf:params:jmap:mail")
    assert "urn:ietf:params:jmap:mail" in server.requests[-1]["using"]


@pytest.mark.parametrize(("text", "message"), [("{", "not valid JSON"), ("[1]", "JSON object")])
def test_bad_arguments(invoke, text, message):
    result = invoke("api", "call", "Core/echo", text)
    assert result.code == 2
    assert message in result.err


def test_a_method_error_exits_6(invoke, server):
    server.fail("Mailbox/get", "invalidArguments", description="nope")
    result = invoke("api", "call", "Mailbox/get", "--json", "-v")
    assert result.code == 6
    assert '"description": "nope"' in result.err
    assert result.err.splitlines()[-1].startswith('{"error": ')


def test_an_unsupported_method_exits_7(invoke, server):
    result = invoke("api", "call", "SieveScript/get")
    assert result.code == 7
    assert "urn:ietf:params:jmap:sieve" in result.err
    assert server.requests == []
