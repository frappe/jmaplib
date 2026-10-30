"""``jmap quota``: list, show, and the bare noun as a shortcut for list."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.servertools import QUOTA_URN, serve_quotas

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@pytest.fixture(autouse=True)
def quotas(server: FakeJMAPServer, account: str) -> None:
    serve_quotas(server)


def test_list(invoke):
    rows = [line.split("\t") for line in invoke("quota", "list").out.splitlines()]
    assert rows[0] == ["q1", "Mail storage", "account", "Mail", "2.0K", "1.0M"]
    assert rows[1] == ["q2", "Messages", "account", "Mail", "12", ""]


def test_the_noun_alone_lists(invoke):
    assert invoke("quota").out == invoke("quota", "list").out


def test_every_shared_capability_is_used(invoke, server):
    invoke("quota", "--json")
    assert {QUOTA_URN, "urn:ietf:params:jmap:mail"} <= set(server.requests[-1]["using"])


class TestShow:
    def test_by_name(self, invoke):
        lines = invoke("quota", "show", "Mail storage").out.splitlines()
        assert "warn at\t878.9K" in lines
        assert "soft limit\t" in lines

    def test_by_id_as_json(self, invoke):
        assert invoke("quota", "show", "q2", "--json").json()["used"] == 12

    def test_an_unknown_quota(self, invoke):
        assert invoke("quota", "show", "q9").code == 9
