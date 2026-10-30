"""``jmap server``: session, capabilities, conformance, echo, refresh."""

from __future__ import annotations

import pytest

from .conftest import CORE_URN, MAIL_URN

pytestmark = pytest.mark.usefixtures("account")


def test_session(invoke):
    out = invoke("server", "session").out
    assert "username\talice@example.com" in out
    assert "capabilities\t2" in out


def test_session_json_is_the_whole_document(invoke, server):
    assert invoke("server", "session", "--json").json() == server.session_document


def test_capabilities_lists_what_is_advertised(invoke):
    rows = invoke("server", "capabilities", "--json").json()
    assert [row["urn"] for row in rows] == [CORE_URN, MAIL_URN]
    assert rows[1]["status"] == "supported"
    assert "Email/get" in rows[1]["methods"]


def test_capabilities_table(invoke):
    assert "RFC 8621" in invoke("server", "capabilities", terminal=True).out


def test_conformance_covers_everything_modelled(invoke):
    rows = invoke("server", "conformance", "--json").json()
    statuses = {row["urn"]: row["status"] for row in rows}
    assert statuses["urn:ietf:params:jmap:sieve"] == "not advertised"


def test_conformance_markdown(invoke):
    assert invoke("server", "conformance", "--markdown").out.startswith("# JMAP conformance")


def test_echo(invoke):
    assert invoke("server", "echo").json() == {"ping": "jmap"}
    assert invoke("server", "echo", '{"a": [1]}').json() == {"a": [1]}


def test_refresh(invoke, server):
    server.session_state = "s9"
    assert invoke("server", "refresh").out == "s9\n"
    assert invoke("server", "refresh", "--json").json() == {"state": "s9"}
