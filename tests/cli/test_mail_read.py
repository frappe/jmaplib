"""``jmap mail read`` and ``thread``."""

from __future__ import annotations

import io

import httpx
import pytest

from jmap.cli.app import run
from tests.fake.mailstore import email

pytestmark = pytest.mark.usefixtures("account")


@pytest.fixture(autouse=True)
def mail(store):
    store.add(
        email(
            "e1",
            "Invoice",
            received="2026-10-28T09:00:00Z",
            attachment=True,
            thread="t1",
            html="<p>Hi <b>there</b></p>",
        ),
        email(
            "e2",
            "Re: Invoice",
            sender="carol@example.com",
            received="2026-10-29T10:00:00Z",
            keywords=("$seen",),
            thread="t1",
        ),
    )
    return store


class TestRead:
    def test_headers_body_attachments_and_marking_seen(self, invoke, store):
        result = invoke("read", "e1")
        assert result.code == 0, result.err
        out = result.out
        assert "From: Bob <bob@example.com>" in out
        assert "To: Alice <alice@example.com>" in out
        assert "Subject: Invoice" in out
        assert "Cc:" not in out
        assert "\n\nHello there.\n" in out
        assert "1. invoice.pdf (application/pdf, 2.0K)" in out
        assert "$seen" in store.emails["e1"]["keywords"]

    def test_html_extra_headers_and_all_headers(self, invoke):
        out = invoke("read", "e1", "--html", "--headers", "List-Id, ,X-None", "--all-headers").out
        assert "Hi there" in out
        assert "List-Id: <list.example.com>" in out
        assert "X-None" not in out
        assert out.count("Subject: Invoice") == 2

    def test_without_marking_seen(self, invoke, store):
        invoke("read", "e1", "--no-mark-seen")
        assert "$seen" not in store.emails["e1"]["keywords"]

    def test_a_read_only_account_is_not_marked(self, invoke, store, server):
        server.accounts["a"]["isReadOnly"] = True
        assert invoke("read", "e1").code == 0
        assert "$seen" not in store.emails["e1"]["keywords"]

    def test_several_messages_are_separated(self, invoke):
        out = invoke("read", "e1", "e2", terminal=True).out
        assert out.count("Subject:") == 2
        assert "─" in out

    def test_json_single_and_list(self, invoke):
        assert invoke("read", "e1", "--json").json()["bodyValues"]["1"]["value"] == "Hello there."
        assert len(invoke("read", "e1", "e2", "--json").json()) == 2

    def test_cut_short_and_badly_decoded_bodies(self, invoke, store):
        values = store.emails["e2"]["bodyValues"]["1"]
        values.update(isTruncated=True, isEncodingProblem=True)
        result = invoke("read", "e2")
        assert "cut short" in result.err
        assert "could not decode" in result.err

    def test_missing_dates_and_bad_dates(self, invoke, store):
        store.emails["e2"]["sentAt"] = "not a date"
        assert "Date: not a date" in invoke("read", "e2").out
        store.emails["e2"]["sentAt"] = None
        store.emails["e2"]["receivedAt"] = None
        assert "Date:" not in invoke("read", "e2").out

    def test_an_unnamed_attachment(self, invoke, store):
        store.emails["e1"]["attachments"][0].pop("name")
        store.emails["e1"]["attachments"][0].pop("type")
        assert "1. (unnamed) (application/octet-stream" in invoke("read", "e1").out


class TestRaw:
    def test_the_source(self, invoke, server):
        server.blobs["blob-e1"] = (b"Subject: Invoice\r\n\r\nHi", "message/rfc822")
        assert invoke("read", "e1", "--raw").out == "Subject: Invoice\r\n\r\nHi"

    def test_to_a_binary_stdout(self, make_context, server):
        server.blobs["blob-e1"] = (b"\xffraw", "message/rfc822")
        context = make_context()
        raw = io.BytesIO()
        context._stdout = io.TextIOWrapper(raw, encoding="utf-8")
        assert run(["read", "e1", "--raw"], context=context) == 0
        assert raw.getvalue() == b"\xffraw"

    def test_one_at_a_time(self, invoke):
        assert invoke("read", "e1", "e2", "--raw").code == 2

    def test_no_source(self, invoke, store):
        store.emails["e1"].pop("blobId")
        assert invoke("read", "e1", "--raw").code == 2
        assert invoke("read", "gone", "--raw").code == 2

    def test_saved_to_a_file(self, invoke, server, tmp_path):
        server.blobs["blob-e1"] = (b"Subject: Invoice\r\n\r\nHi", "message/rfc822")
        target = tmp_path / "m.eml"
        result = invoke("read", "e1", "--raw", "--save", str(target))
        assert result.code == 0, result.err
        assert f"saved {target}" in result.err
        assert target.read_bytes() == b"Subject: Invoice\r\n\r\nHi"
        assert invoke("read", "e1", "--raw", "--save", str(target)).code == 2
        assert invoke("read", "e1", "--raw", "--save", str(target), "--force").code == 0


class TestPart:
    def test_one_part_by_its_id(self, invoke, server):
        server.blobs["b-e1-3"] = (b"%PDF-1.7", "application/pdf")
        assert invoke("read", "e1", "--part", "3").out == "%PDF-1.7"
        call = server.requests[-1]["methodCalls"][0][1]
        assert call["properties"] == ["bodyStructure"]

    def test_an_unknown_part_names_the_others(self, invoke):
        result = invoke("read", "e1", "--part", "9")
        assert result.code == 9
        assert "message e1 has no part '9'" in result.err
        assert "1 (text/plain), 2 (text/html), 3 (application/pdf)" in result.err

    def test_a_missing_message_has_no_parts(self, invoke):
        result = invoke("read", "gone", "--part", "1")
        assert result.code == 9
        assert "its parts are" not in result.err

    def test_one_at_a_time(self, invoke):
        assert invoke("read", "e1", "e2", "--part", "1").code == 2


class TestThread:
    def test_oldest_first_and_numbered(self, invoke):
        result = invoke("thread", "e2")
        rows = [line.split("\t") for line in result.out.splitlines()]
        assert [(row[0], row[4]) for row in rows] == [("1", "Invoice"), ("2", "Re: Invoice")]
        assert invoke("read", "2", "--json").json()["id"] == "e2"

    def test_read_every_message(self, invoke, store):
        out = invoke("thread", "e2", "--read", terminal=True).out
        assert out.count("Subject:") == 2
        assert "$seen" not in store.emails["e1"]["keywords"]

    def test_json(self, invoke):
        assert [item["id"] for item in invoke("thread", "e1", "--json").json()] == ["e1", "e2"]

    def test_table(self, invoke):
        assert "Re: Invoice" in invoke("thread", "e1", terminal=True).out

    def test_falls_back_when_the_server_cannot_flatten(self, invoke, server):
        def refuse(request: httpx.Request) -> httpx.Response | None:
            # Answers the one-request form as a server that cannot flatten would.
            if b"/list/*/emailIds" in request.content:
                answer = {
                    "methodResponses": [
                        [
                            "Email/get",
                            {
                                "accountId": "a",
                                "list": [{"id": "e2", "threadId": "t1"}],
                                "notFound": [],
                            },
                            "c1",
                        ],
                        ["Thread/get", {"accountId": "a", "list": [], "notFound": []}, "c2"],
                        ["error", {"type": "invalidResultReference"}, "c3"],
                    ],
                    "sessionState": "session-0",
                }
                return httpx.Response(200, json=answer)
            return None

        server.intercept = refuse
        rows = [line.split("\t") for line in invoke("thread", "e2").out.splitlines()]
        assert [row[4] for row in rows] == ["Invoice", "Re: Invoice"]

    def test_other_errors_are_reported(self, invoke, server):
        server.fail("Thread/get", "serverFail")
        assert invoke("thread", "e2").code == 6
