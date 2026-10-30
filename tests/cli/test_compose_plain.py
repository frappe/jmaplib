"""Writing on a server that offers mail but not submission: drafts only."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("account")


def test_a_draft_can_still_be_saved(invoke, store):
    result = invoke("compose", "--to", "b@x.org", "--body", "later", "--draft")
    assert result.code == 0, result.err
    assert list(store.emails.values())[-1]["sent"].get("from") is None


def test_sending_needs_submission(invoke, store):
    result = invoke("send", "--to", "b@x.org", "--body", "x")
    assert result.code == 7
    assert "urn:ietf:params:jmap:submission" in result.err
