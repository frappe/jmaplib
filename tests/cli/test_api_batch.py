"""``jmap api batch``: a file of calls, sent as one request."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("account")


def calls(*items: Any) -> str:
    return json.dumps(list(items))


def test_a_back_reference_by_the_files_call_id(invoke, server, tmp_path):
    source = tmp_path / "calls.json"
    source.write_text(
        calls(
            ["Mailbox/get", {"ids": ["m1"]}, "boxes"],
            ["Core/echo", {"#seen": {"resultOf": "boxes", "path": "/list/0/name"}}, "echo"],
        ),
        encoding="utf-8",
    )
    result = invoke("api", "batch", str(source))
    assert result.code == 0, result.err
    [boxes, echo] = result.json()
    assert boxes[0] == "Mailbox/get"
    assert boxes[2] == "boxes"
    assert echo == ["Core/echo", {"seen": "Inbox"}, "echo"]
    [request] = server.requests
    assert request["methodCalls"][1][1]["#seen"]["resultOf"] == request["methodCalls"][0][2]


def test_call_ids_default_to_their_position(invoke):
    result = invoke("api", "batch", "-", stdin=calls(["Core/echo", {"x": 1}]))
    assert result.json() == [["Core/echo", {"x": 1}, "c1"]]


def test_a_method_error_is_printed_then_exits_6(invoke, server):
    server.fail("Core/echo", "invalidArguments", description="no")
    result = invoke(
        "api", "batch", "-", stdin=calls(["Mailbox/get", {"ids": []}], ["Core/echo", {}])
    )
    assert result.code == 6
    assert result.json()[1] == ["error", {"type": "invalidArguments", "description": "no"}, "c2"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[", "not valid JSON"),
        ("{}", "a JSON array"),
        ("[]", "a JSON array"),
        ('["Core/echo"]', "call 1 is not"),
        ("[[1, {}]]", "call 1 is not"),
        ('[["Core/echo", []]]', "must be a JSON object"),
        ('[["Core/echo", {}, "a"], ["Core/echo", {}, "a"]]', "used twice"),
        ('[["Core/echo", {"#x": {"resultOf": "nope", "path": "/x"}}]]', "earlier call"),
        ('[["Core/echo", {"#x": "c1"}]]', "earlier call"),
    ],
)
def test_malformed_calls(invoke, text, message):
    result = invoke("api", "batch", "-", stdin=text)
    assert result.code == 2
    assert message in result.err


def test_a_missing_file(invoke, tmp_path):
    assert "cannot read" in invoke("api", "batch", str(tmp_path / "none.json")).err


def test_extra_using(invoke, server):
    invoke(
        "api", "batch", "-", "--using", "urn:ietf:params:jmap:mail", stdin=calls(["Core/echo", {}])
    )
    assert "urn:ietf:params:jmap:mail" in server.requests[0]["using"]
