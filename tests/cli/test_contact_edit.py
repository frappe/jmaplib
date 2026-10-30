"""``jmap contact edit`` and ``jmap contact import``."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

import pytest

from jmap.cli.app import run
from jmap.cli.commands.contact_edit import top_level_patch
from jmap.cli.commands.contact_import import in_halves, split_vcards
from jmap.core.errors import MethodError
from tests.fake.contactstore import ContactStore, card

from .conftest import ScriptedPrompter, text_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from jmap.testing import FakeJMAPServer


@pytest.fixture
def contacts(server: FakeJMAPServer, account: str) -> ContactStore:
    store = ContactStore(server)
    store.add(card("c1", "Ada Lovelace", "ada@example.com", notes={"n1": {"note": "x"}}))
    return store


def edit_json(change: Callable[[dict[str, Any]], object]) -> Callable[[str], str]:
    """An editor change that works on the card as JSON."""

    def apply(text: str) -> str:
        value = json.loads(text)
        change(value)
        return json.dumps(value)

    return apply


class TestEdit:
    def test_sends_only_what_changed(self, invoke, contacts, server, editor):
        def change(value):
            value["name"] = {"full": "Augusta Ada King"}
            del value["notes"]

        fake = editor(edit_json(change))
        result = invoke("contact", "edit", "ada@example.com")
        assert result.code == 0, result.err
        assert '"full": "Ada Lovelace"' in fake.seen[0]
        [call] = server.requests[-1]["methodCalls"]
        assert call[1]["update"] == {"c1": {"name": {"full": "Augusta Ada King"}, "notes": None}}
        assert "updated Augusta Ada King" in result.err
        assert "notes" not in contacts.cards["c1"]

    def test_unchanged(self, invoke, contacts, editor):
        editor(lambda text: text)
        result = invoke("contact", "edit", "c1")
        assert result.code == 1
        assert "not changed" in result.err

    def test_only_layout_changed(self, invoke, contacts, editor):
        editor(lambda text: json.dumps(json.loads(text)))
        assert "not changed" in invoke("contact", "edit", "c1").err

    def test_bad_json_is_edited_again(self, invoke, contacts, editor):
        fake = editor(lambda text: text + "}", lambda text: text[:-1].replace("Ada", "Ida", 1))
        prompter = ScriptedPrompter(confirms=[True])
        result = invoke("contact", "edit", "c1", prompter=prompter)
        assert result.code == 0, result.err
        assert prompter.asked[0].startswith("the card is not valid JSON")
        assert fake.seen[1].endswith("}")
        assert contacts.cards["c1"]["name"] == {"full": "Ida Lovelace"}

    def test_not_an_object_without_a_terminal(self, invoke, contacts, editor):
        editor(lambda text: "[]")
        result = invoke("contact", "edit", "c1")
        assert result.code == 2
        assert "the card must be a JSON object" in result.err

    def test_a_refused_change(self, invoke, contacts, editor):
        contacts.refuse["c1"] = "invalidProperties"
        editor(edit_json(lambda value: value.update(kind="org")))
        result = invoke("contact", "edit", "c1")
        assert result.code == 8
        assert "c1: invalidProperties" in result.err


def test_the_patch_is_top_level():
    old = {"id": "c1", "name": {"full": "A"}, "kind": "individual"}
    new = {"id": "c1", "name": {"full": "B"}, "uid": "u"}
    assert top_level_patch(old, new) == {"name": {"full": "B"}, "uid": "u", "kind": None}
    assert top_level_patch(old, {"name": {"full": "A"}, "kind": "individual"}) == {}
    with pytest.raises(Exception, match="id cannot change"):
        top_level_patch(old, {**old, "id": "c2"})


VCF = (
    b"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Grace Hopper\r\nEMAIL:grace@navy.mil\r\nEND:VCARD\r\n"
    b"begin:vcard\nVERSION:3.0\nFN:Alan Turing\nend:vcard\n"
    b"BEGIN:VCARD\r\nVERSION:4.0\r\nNOTE:no name\r\nEND:VCARD\r\n"
    b"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Joan Clarke\r\nEND:VCARD"
)


class TestImport:
    def test_splits_parses_and_creates(self, invoke, contacts, server, tmp_path):
        path = tmp_path / "people.vcf"
        path.write_bytes(VCF)
        result = invoke("contact", "import", str(path), "--book", "Work")
        assert result.code == 8
        assert "card3: the server could not read it" in result.err
        assert "imported 3 contact(s) into Work" in result.err
        assert "1 card(s) not read: card3" in result.err
        names = [c["name"]["full"] for c in contacts.cards.values() if c["id"] != "c1"]
        assert names == ["Grace Hopper", "Alan Turing", "Joan Clarke"]
        assert all(
            c["addressBookIds"] == {"b2": True} for c in contacts.cards.values() if c["id"] != "c1"
        )
        uploaded = [blob for blob, _ in server.blobs.values()]
        assert uploaded[1] == b"begin:vcard\nVERSION:3.0\nFN:Alan Turing\nend:vcard\r\n"

    def test_halves_on_request_too_large(self, invoke, contacts, server, make_context):
        contacts.max_parse = 1
        contacts.max_set = 2
        context = make_context()
        context.stdin = io.TextIOWrapper(io.BytesIO(VCF.replace(b"NOTE:no name", b"FN:Mary")))
        code = run(["contact", "import", "-", "--json"], context=context)
        assert code == 0, text_of(context.err.file)
        response = json.loads(text_of(context.out.file))
        assert sorted(response["created"]) == ["card1", "card2", "card3", "card4"]
        calls = [body["methodCalls"][0][0] for body in server.requests]
        assert calls.count("ContactCard/parse") == 4
        assert calls.count("ContactCard/set") == 2

    def test_from_a_text_stdin(self, invoke, contacts):
        result = invoke("contact", "import", "-", stdin="BEGIN:VCARD\nFN:Text\nEND:VCARD\n")
        assert result.code == 0, result.err
        assert "imported 1 contact(s) into Personal" in result.err

    def test_none_readable(self, invoke, contacts):
        stdin = "BEGIN:VCARD\nNOTE:x\nEND:VCARD\n"
        result = invoke("contact", "import", "-", stdin=stdin)
        assert result.code == 8
        assert "imported" not in result.err

    def test_no_vcard(self, invoke, contacts):
        result = invoke("contact", "import", "-", stdin="hello\n")
        assert result.code == 2
        assert "- holds no vCard" in result.err

    def test_a_missing_file(self, invoke, contacts, tmp_path):
        result = invoke("contact", "import", str(tmp_path / "nope.vcf"))
        assert result.code == 2
        assert "cannot read" in result.err

    def test_needs_the_parse_capability(self, invoke, contacts, server):
        del server.capabilities["urn:ietf:params:jmap:contacts:parse"]
        result = invoke("contact", "import", "-", stdin="BEGIN:VCARD\nFN:A\nEND:VCARD\n")
        assert result.code == 7


def test_split_vcards_keeps_each_card_whole():
    assert split_vcards(b"junk\nBEGIN:VCARD\nFN:A\nEND:VCARD\njunk") == [
        b"BEGIN:VCARD\nFN:A\nEND:VCARD\r\n"
    ]
    assert split_vcards(b"no cards") == []


class TestInHalves:
    @staticmethod
    def sender(
        limit: int, error: str = "requestTooLarge"
    ) -> tuple[Callable[[list[int]], int], list[list[int]]]:
        sent: list[list[int]] = []

        def send(chunk: list[int]) -> int:
            if len(chunk) > limit:
                raise MethodError(error, "c1", {})
            sent.append(chunk)
            return sum(chunk)

        return send, sent

    def test_halves_until_it_fits(self):
        send, sent = self.sender(2)
        assert in_halves([1, 2, 3, 4, 5], 5, send) == [3, 7, 5]
        assert sent == [[1, 2], [3, 4], [5]]

    def test_one_item_too_large(self):
        send, _ = self.sender(0)
        with pytest.raises(MethodError):
            in_halves([1], 1, send)

    def test_other_errors_pass(self):
        send, _ = self.sender(0, "forbidden")
        with pytest.raises(MethodError, match="forbidden"):
            in_halves([1, 2], 2, send)
