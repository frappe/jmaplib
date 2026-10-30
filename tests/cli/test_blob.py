"""``jmap blob``: upload, download, info, lookup and copy."""

from __future__ import annotations

import base64
import hashlib
import io
from typing import TYPE_CHECKING

import pytest

from jmap.cli.app import run
from tests.fake.servertools import serve_blob_methods

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@pytest.fixture(autouse=True)
def blobs(server: FakeJMAPServer, account: str) -> FakeJMAPServer:
    serve_blob_methods(server, type_names=["Email", "Mailbox", "Private"])
    server.blobs["B9"] = (b"hello", "text/plain")
    return server


class TestUpload:
    def test_a_file(self, invoke, tmp_path, server):
        path = tmp_path / "notes.txt"
        path.write_bytes(b"some notes")
        result = invoke("blob", "upload", str(path), "--json")
        assert result.code == 0, result.err
        uploaded = result.json()
        assert uploaded["type"] == "text/plain"
        assert server.blobs[uploaded["blobId"]] == (b"some notes", "text/plain")

    def test_stdin_with_a_type(self, invoke, server):
        result = invoke("blob", "upload", "-", "--type", "text/x-note", stdin="from stdin")
        assert "blob\t" in result.out
        assert (b"from stdin", "text/x-note") in server.blobs.values()

    def test_binary_stdin(self, make_context, server):
        context = make_context()
        context.stdin = io.TextIOWrapper(io.BytesIO(b"\xff\x00"))
        assert run(["blob", "upload", "-"], context=context) == 0
        assert (b"\xff\x00", "application/octet-stream") in server.blobs.values()

    def test_a_missing_file(self, invoke, tmp_path):
        result = invoke("blob", "upload", str(tmp_path / "nope"))
        assert result.code == 2
        assert "cannot read" in result.err


class TestDownload:
    def test_to_stdout(self, invoke):
        assert invoke("blob", "download", "B9").out == "hello"

    def test_to_a_file(self, invoke, tmp_path):
        path = tmp_path / "out.txt"
        result = invoke("blob", "download", "B9", "--save", str(path))
        assert path.read_bytes() == b"hello"
        assert f"saved {path}" in result.err
        assert invoke("blob", "download", "B9", "--save", str(path)).code == 2
        assert invoke("blob", "download", "B9", "--save", str(path), "--force").code == 0

    def test_not_to_a_terminal(self, invoke):
        result = invoke("blob", "download", "B9", terminal=True)
        assert result.code == 2
        assert "--save" in result.err

    def test_to_stdout_on_a_terminal_when_asked(self, invoke):
        assert "hello" in invoke("blob", "download", "B9", "--save", "-", terminal=True).out


class TestInfo:
    def test_size_and_digest(self, invoke):
        result = invoke("blob", "info", "B9", "--digest", "SHA-256")
        expected = base64.b64encode(hashlib.sha256(b"hello").digest()).decode()
        assert result.out.split() == ["B9", "5B", expected]

    def test_json(self, invoke):
        [item] = invoke("blob", "info", "B9", "--json").json()
        assert item == {"id": "B9", "size": 5}

    def test_an_unknown_blob(self, invoke):
        result = invoke("blob", "info", "B9", "B404")
        assert result.code == 9
        assert "no blob B404" in result.err

    def test_an_unoffered_digest(self, invoke):
        assert invoke("blob", "info", "B9", "--digest", "md5").code == 2


class TestLookup:
    def test_the_types_the_server_and_this_build_share(self, invoke, server):
        result = invoke("blob", "lookup", "B9")
        assert result.out.splitlines() == ["B9\tEmail\tE-B9", "B9\tMailbox\tM-B9"]
        assert server.requests[-1]["methodCalls"][0][1]["typeNames"] == ["Email", "Mailbox"]

    def test_named_types_as_json(self, invoke):
        result = invoke("blob", "lookup", "B9", "--type", "Email", "--json")
        assert result.json() == [{"id": "B9", "matchedIds": {"Email": ["E-B9"]}}]

    def test_an_unknown_blob(self, invoke):
        assert invoke("blob", "lookup", "B404", "--type", "Email").code == 9

    def test_a_server_that_looks_nothing_up(self, invoke, server):
        server.capabilities["urn:ietf:params:jmap:blob"]["supportedTypeNames"] = []
        result = invoke("blob", "lookup", "B9")
        assert result.code == 7
        assert "no data types" in result.err


class TestCopy:
    def test_copied(self, invoke):
        result = invoke("blob", "copy", "B9", "--from-account", "other")
        assert result.out.split() == ["B9", "B9-copy"]

    def test_json_and_refusals(self, invoke):
        result = invoke("blob", "copy", "B9", "B404", "--from-account", "other", "--json")
        assert result.code == 8
        assert result.json()["copied"] == {"B9": "B9-copy"}
        assert "B404: blobNotFound" in result.err

    def test_needs_the_source_account(self, invoke):
        assert invoke("blob", "copy", "B9").code == 2
