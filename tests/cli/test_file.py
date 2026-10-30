"""``jmap file``: ls, tree, upload, download, mkdir, rm and mv."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fake.filestore import FileStore

if TYPE_CHECKING:
    from pathlib import Path

    from jmap.testing import FakeJMAPServer


@pytest.fixture
def files(server: FakeJMAPServer, account: str) -> FileStore:
    store = FileStore(server)
    store.add("d1", "Projects")
    store.add("d2", "Old", "d1")
    store.file("f1", "notes.txt", "d1", b"hello")
    store.file("f2", "readme.md", None, b"# hi")
    store.file("f3", "plan.txt", "d2", b"later")
    return store


def rows(out: str) -> list[list[str]]:
    return [line.split("\t") for line in out.splitlines()]


class TestLs:
    def test_the_top_level(self, invoke, files):
        result = invoke("file", "ls")
        assert result.code == 0, result.err
        assert [row[2] for row in rows(result.out)] == ["Projects/", "readme.md"]

    def test_a_directory_directories_first(self, invoke, files, server):
        result = invoke("file", "ls", "/Projects")
        assert [row[2] for row in rows(result.out)] == ["Old/", "notes.txt"]
        assert rows(result.out)[1][:1] == ["5B"]
        queries = [
            call[1]["filter"]
            for body in server.requests
            for call in body["methodCalls"]
            if call[0] == "FileNode/query"
        ]
        # The top level is asked for without "parentId": null, which Stalwart refuses.
        assert queries == [{"name": "Projects"}, {"parentId": "d1"}]

    def test_a_file(self, invoke, files):
        result = invoke("file", "ls", "Projects/./notes.txt", "--json")
        assert [item["id"] for item in result.json()] == ["f1"]

    def test_not_found(self, invoke, files):
        result = invoke("file", "ls", "/Projects/Nope/x")
        assert result.code == 9
        assert "no file or directory /Projects/Nope" in result.err

    def test_unsupported_without_the_capability(self, invoke, account):
        assert invoke("file", "ls").code == 7


def test_tree(invoke, files):
    result = invoke("file", "tree")
    assert [row[2] for row in rows(result.out)] == [
        "Projects/",
        "  Old/",
        "    plan.txt",
        "  notes.txt",
        "readme.md",
    ]


def test_tree_of_a_directory(invoke, files, server):
    result = invoke("file", "tree", "/Projects")
    assert [row[2] for row in rows(result.out)] == ["Old/", "  plan.txt", "notes.txt"]
    query = server.requests[-1]["methodCalls"][0][1]
    assert "filter" not in query
    assert "depth" not in query


def test_tree_keeps_its_depth_at_the_top(invoke, files):
    result = invoke("file", "tree", "--depth", "2")
    assert [row[2] for row in rows(result.out)] == [
        "Projects/",
        "  Old/",
        "  notes.txt",
        "readme.md",
    ]


def test_tree_of_a_file_is_refused(invoke, files):
    result = invoke("file", "tree", "/readme.md")
    assert result.code == 2
    assert "/readme.md is not a directory" in result.err


class TestUpload:
    @pytest.fixture
    def local(self, tmp_path: Path) -> Path:
        path = tmp_path / "report.pdf"
        path.write_bytes(b"%PDF")
        return path

    def test_to_the_top_level(self, invoke, files, server, local):
        result = invoke("file", "upload", str(local))
        assert result.code == 0, result.err
        assert "uploaded report.pdf" in result.err
        node = files.last_set["create"]["file"]
        assert node["parentId"] is None
        assert node["type"] == "application/pdf"
        assert server.blobs[node["blobId"]][0] == b"%PDF"

    def test_into_a_directory(self, invoke, files, local):
        invoke("file", "upload", str(local), "/Projects")
        assert files.last_set["create"]["file"]["parentId"] == "d1"
        assert files.last_set["create"]["file"]["name"] == "report.pdf"

    def test_into_a_directory_by_slash(self, invoke, files, local):
        invoke("file", "upload", str(local), "/Projects/Old/")
        assert files.last_set["create"]["file"]["parentId"] == "d2"

    def test_under_a_new_name(self, invoke, files, local):
        invoke("file", "upload", str(local), "/Projects/q3.bin")
        node = files.last_set["create"]["file"]
        assert (node["parentId"], node["name"]) == ("d1", "q3.bin")
        assert node["type"] == "application/octet-stream"

    def test_a_name_taken(self, invoke, files, local):
        result = invoke("file", "upload", str(local), "/Projects/notes.txt")
        assert result.code == 8
        assert "alreadyExists" in result.err

    def test_replace(self, invoke, files, local):
        result = invoke("file", "upload", str(local), "/Projects/notes.txt", "--replace")
        assert result.code == 0, result.err
        assert files.last_set["onExists"] == "replace"
        assert "f1" not in files.nodes

    def test_into_a_file_is_refused(self, invoke, files, local):
        result = invoke("file", "upload", str(local), "/readme.md/x")
        assert result.code == 2
        assert "is not a directory" in result.err

    def test_unreadable(self, invoke, files, tmp_path):
        result = invoke("file", "upload", str(tmp_path / "missing"))
        assert result.code == 2
        assert "cannot read" in result.err


class TestDownload:
    def test_to_a_directory(self, invoke, files, tmp_path):
        result = invoke("file", "download", "/Projects/notes.txt", "--dir", str(tmp_path))
        assert result.code == 0, result.err
        assert (tmp_path / "notes.txt").read_bytes() == b"hello"
        invoke("file", "download", "/Projects/notes.txt", "--dir", str(tmp_path))
        assert (tmp_path / "notes (1).txt").read_bytes() == b"hello"

    def test_to_a_file(self, invoke, files, tmp_path):
        target = tmp_path / "copy.md"
        invoke("file", "download", "readme.md", "--save", str(target))
        assert target.read_bytes() == b"# hi"
        result = invoke("file", "download", "readme.md", "--save", str(target))
        assert result.code == 2
        assert "pass --force" in result.err

    def test_to_stdout(self, invoke, files):
        assert invoke("file", "download", "readme.md", "--save", "-").out == "# hi"

    def test_a_directory_is_refused(self, invoke, files):
        result = invoke("file", "download", "/Projects")
        assert result.code == 2
        assert "is not a file" in result.err


class TestMkdir:
    def test_one(self, invoke, files):
        result = invoke("file", "mkdir", "/Projects/New")
        assert result.code == 0, result.err
        assert "created 1 directory" in result.err
        assert files.last_set["create"] == {"d1": {"parentId": "d1", "name": "New"}}

    def test_parents(self, invoke, files):
        result = invoke("file", "mkdir", "-p", "/Projects/a/b")
        assert "created 2 directories" in result.err
        assert files.last_set["create"]["d2"] == {"parentId": "#d1", "name": "b"}
        assert files.path_of(max(files.nodes)) == "/Projects/a/b"

    def test_missing_parents_need_the_option(self, invoke, files):
        result = invoke("file", "mkdir", "/a/b")
        assert result.code == 2
        assert "pass --parents" in result.err

    def test_existing(self, invoke, files):
        assert invoke("file", "mkdir", "/Projects").code == 2
        assert invoke("file", "mkdir", "--parents", "/Projects").code == 0

    def test_through_a_file(self, invoke, files):
        result = invoke("file", "mkdir", "/readme.md/x")
        assert result.code == 2
        assert "/readme.md is a file" in result.err


class TestRm:
    def test_a_file(self, invoke, files):
        result = invoke("file", "rm", "/readme.md", "--yes")
        assert "deleted /readme.md" in result.err
        assert "f2" not in files.nodes

    def test_a_full_directory_needs_recursive(self, invoke, files):
        result = invoke("file", "rm", "/Projects", "--yes")
        assert result.code == 8
        assert "nodeHasChildren" in result.err

    def test_recursive(self, invoke, files):
        result = invoke("file", "rm", "-r", "/Projects", "--yes")
        assert result.code == 0, result.err
        assert files.last_set["onDestroyRemoveChildren"] is True
        assert set(files.nodes) == {"f2"}

    def test_confirms(self, invoke, files):
        assert invoke("file", "rm", "/readme.md").code == 1
        assert "f2" in files.nodes

    def test_the_top_level(self, invoke, files):
        assert invoke("file", "rm", "/", "--yes").code == 2


class TestMv:
    def test_into_a_directory(self, invoke, files):
        result = invoke("file", "mv", "/readme.md", "/Projects")
        assert result.code == 0, result.err
        assert files.path_of("f2") == "/Projects/readme.md"

    def test_rename(self, invoke, files):
        invoke("file", "mv", "/Projects/Old", "/Archive")
        assert files.path_of("d2") == "/Archive"
        assert files.path_of("f3") == "/Archive/plan.txt"

    def test_replace(self, invoke, files):
        invoke("file", "mv", "/readme.md", "/Projects/notes.txt", "--replace")
        assert files.last_set["onExists"] == "replace"

    def test_the_top_level(self, invoke, files):
        assert invoke("file", "mv", "/", "/x").code == 2
