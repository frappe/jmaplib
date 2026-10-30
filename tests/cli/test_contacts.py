"""``jmap addressbook`` and ``jmap contact list/show/create/delete``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from jmap.cli.commands.cards import addresses, display_name, emails, notes, organization
from tests.fake.contactstore import ContactStore, card

from .conftest import ScriptedPrompter

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer


@pytest.fixture
def contacts(server: FakeJMAPServer, account: str) -> ContactStore:
    store = ContactStore(server)
    store.add(
        card("c1", "Ada Lovelace", "ada@example.com", phones={"p1": {"number": "+44 1"}}),
        card("c2", "Charles Babbage", "charles@example.com", book="b2"),
    )
    return store


def rows(text: str) -> list[list[str]]:
    return [line.split("\t") for line in text.splitlines()]


class TestAddressBook:
    def test_list(self, invoke, contacts):
        result = invoke("addressbook", "list")
        assert rows(result.out) == [
            ["b1", "Personal", "yes", "yes", ""],
            ["b2", "Work", "", "yes", ""],
        ]

    def test_create(self, invoke, contacts):
        result = invoke("addressbook", "create", "Family")
        assert "created the address book Family" in result.err
        assert [book["name"] for book in contacts.books.values()][-1] == "Family"
        assert contacts.books["b1"]["isDefault"]

    def test_create_as_default_with_a_description(self, invoke, contacts, server):
        invoke("addressbook", "create", "Family", "--description", "Kin", "--default")
        [created] = [book for book in contacts.books.values() if book["name"] == "Family"]
        assert created["description"] == "Kin"
        assert created["isDefault"]
        assert not contacts.books["b1"]["isDefault"]
        [call] = server.requests[-1]["methodCalls"]
        assert call[1]["onSuccessSetIsDefault"] == "#new"

    def test_rename_by_name_in_any_case(self, invoke, contacts):
        assert "renamed Work to Office" in invoke("addressbook", "rename", "work", "Office").err
        assert contacts.books["b2"]["name"] == "Office"

    def test_rename_by_id(self, invoke, contacts):
        invoke("addressbook", "rename", "b1", "Home")
        assert contacts.books["b1"]["name"] == "Home"

    def test_an_unknown_book(self, invoke, contacts):
        result = invoke("addressbook", "rename", "Nope", "X")
        assert result.code == 9
        assert "see 'jmap addressbook list'" in result.err

    def test_two_books_with_one_name(self, invoke, contacts):
        contacts.books["b3"] = {"id": "b3", "name": "work"}
        result = invoke("addressbook", "rename", "Work", "X")
        assert result.code == 2
        assert "2 address books are named 'Work'" in result.err

    def test_delete_refuses_a_book_with_cards(self, invoke, contacts):
        result = invoke("addressbook", "delete", "Work", "--yes")
        assert result.code == 8
        assert "addressBookHasContents" in result.err
        assert "b2" in contacts.books

    def test_delete_with_its_contacts(self, invoke, contacts):
        result = invoke("addressbook", "delete", "Work", "--with-contacts", "--yes")
        assert result.code == 0, result.err
        assert "b2" not in contacts.books
        assert "c2" not in contacts.cards

    def test_delete_asks_first(self, invoke, contacts):
        prompter = ScriptedPrompter(confirms=[False])
        result = invoke("addressbook", "delete", "Work", prompter=prompter)
        assert result.code == 1
        assert prompter.asked == ["Delete the address book Work?"]

    def test_a_server_without_contacts(self, invoke, account):
        result = invoke("addressbook", "list")
        assert result.code == 7
        assert "urn:ietf:params:jmap:contacts" in result.err


class TestList:
    def test_every_card(self, invoke, contacts):
        result = invoke("contact", "list")
        assert rows(result.out) == [
            ["c1", "Ada Lovelace", "ada@example.com", "+44 1", ""],
            ["c2", "Charles Babbage", "charles@example.com", "", ""],
        ]
        assert "2 of 2 contact(s)" in result.err

    def test_text_book_sort_and_limit(self, invoke, contacts, server):
        result = invoke("contact", "list", "a", "--book", "Work", "--sort", "-surname", "-n", "5")
        assert [row[0] for row in rows(result.out)] == ["c2"]
        query, get = server.requests[-1]["methodCalls"]
        assert query[1]["filter"] == {"text": "a", "inAddressBook": "b2"}
        assert query[1]["sort"] == [{"property": "name/surname", "isAscending": False}]
        assert query[1]["limit"] == 5
        assert get[1]["#ids"]["resultOf"] == query[2]

    def test_sorted_by_given_name(self, invoke, contacts):
        result = invoke("contact", "list", "--sort", "given")
        assert [row[1] for row in rows(result.out)] == ["Ada Lovelace", "Charles Babbage"]

    def test_an_unknown_sort(self, invoke, contacts):
        result = invoke("contact", "list", "--sort", "age")
        assert result.code == 2
        assert "cannot sort by 'age'" in result.err

    def test_without_a_total(self, invoke, contacts):
        contacts.totals = False
        result = invoke("contact", "list")
        assert "contact(s)" not in result.err

    def test_json_is_the_cards(self, invoke, contacts):
        cards = invoke("contact", "list", "--json").json()
        assert [item["id"] for item in cards] == ["c1", "c2"]
        assert cards[0]["emails"] == {"e1": {"address": "ada@example.com"}}


class TestShow:
    def test_by_address(self, invoke, contacts):
        contacts.cards["c1"].update(
            kind="individual",
            notes={"n1": {"note": "Analyst"}},
            addresses={"a1": {"full": "12 St James's Square, London"}},
            organizations={"o1": {"name": "Analytical Society"}},
        )
        lines = dict(
            line.split("\t", 1)
            for line in invoke("contact", "show", "ada@example.com").out.splitlines()
        )
        assert lines["name"] == "Ada Lovelace"
        assert lines["kind"] == "individual"
        assert lines["phones"] == "+44 1"
        assert lines["addresses"] == "12 St James's Square, London"
        assert lines["notes"] == "Analyst"
        assert lines["organization"] == "Analytical Society"
        assert lines["address books"] == "Personal"

    def test_by_id_as_json(self, invoke, contacts):
        assert invoke("contact", "show", "c2", "--json").json()["id"] == "c2"

    def test_a_book_that_is_gone_shows_its_id(self, invoke, contacts):
        contacts.cards["c1"]["addressBookIds"] = {"gone": True, "b1": False}
        result = invoke("contact", "show", "c1")
        assert "address books\tgone" in result.out

    def test_no_such_id(self, invoke, contacts):
        result = invoke("contact", "show", "c9")
        assert result.code == 9
        assert "no contact c9" in result.err

    def test_no_such_address(self, invoke, contacts):
        result = invoke("contact", "show", "nobody@example.com")
        assert result.code == 9
        assert "no contact has the address nobody@example.com" in result.err

    def test_an_address_two_cards_share(self, invoke, contacts):
        contacts.add(card("c3", "Ada Again", "ada@example.com"))
        result = invoke("contact", "show", "ada@example.com")
        assert result.code == 2
        assert "2 contacts have ada@example.com: c1, c3" in result.err


class TestCreate:
    def test_in_the_default_book(self, invoke, contacts):
        result = invoke(
            "contact", "create", "--given", "Grace", "--surname", "Hopper",
            "--email", "grace@navy.mil", "--email", "g@x.org", "--phone", "+1 2",
            "--org", "Navy", "--title", "Rear Admiral", "--note", "COBOL", "--kind", "Individual",
        )  # fmt: skip
        assert result.code == 0, result.err
        assert "created Grace Hopper in Personal" in result.err
        created = contacts.cards["cnew1"]
        assert created["@type"] == "Card"
        assert created["uid"].startswith("urn:uuid:")
        assert created["addressBookIds"] == {"b1": True}
        assert created["name"] == {
            "full": "Grace Hopper",
            "components": [
                {"kind": "given", "value": "Grace"},
                {"kind": "surname", "value": "Hopper"},
            ],
        }
        assert created["emails"]["e2"] == {"address": "g@x.org"}
        assert created["phones"] == {"p1": {"number": "+1 2"}}
        assert created["titles"] == {"t1": {"name": "Rear Admiral", "organizationId": "o1"}}
        assert created["notes"] == {"n1": {"note": "COBOL"}}
        assert created["kind"] == "individual"

    def test_in_a_named_book_with_a_full_name(self, invoke, contacts):
        invoke("contact", "create", "--name", "Alan Turing", "--title", "Fellow", "--book", "Work")
        created = contacts.cards["cnew1"]
        assert created["addressBookIds"] == {"b2": True}
        assert created["name"] == {"full": "Alan Turing"}
        assert created["titles"] == {"t1": {"name": "Fellow"}}

    def test_with_only_an_address(self, invoke, contacts):
        result = invoke("contact", "create", "--email", "x@example.com")
        assert "created x@example.com" in result.err
        assert "name" not in contacts.cards["cnew1"]

    def test_the_first_book_when_none_is_default(self, invoke, contacts):
        contacts.books["b1"]["isDefault"] = False
        invoke("contact", "create", "--org", "Acme")
        assert contacts.cards["cnew1"]["addressBookIds"] == {"b1": True}

    def test_no_book_at_all(self, invoke, contacts):
        contacts.books.clear()
        result = invoke("contact", "create", "--org", "Acme")
        assert result.code == 9
        assert "jmap addressbook create" in result.err

    def test_nothing_to_create(self, invoke, contacts):
        result = invoke("contact", "create", "--phone", "+1 2")
        assert result.code == 2


class TestDelete:
    def test_after_confirming(self, invoke, contacts):
        prompter = ScriptedPrompter(confirms=[True])
        result = invoke("contact", "delete", "c1", "charles@example.com", prompter=prompter)
        assert result.code == 0, result.err
        assert prompter.asked == ["Delete 2 contact(s): Ada Lovelace, Charles Babbage?"]
        assert "deleted 2 contact(s)" in result.err
        assert not contacts.cards

    def test_needs_yes_without_a_terminal(self, invoke, contacts):
        result = invoke("contact", "delete", "c1")
        assert result.code == 1
        assert "pass --yes" in result.err
        assert "c1" in contacts.cards


class TestLegacy:
    @pytest.fixture
    def legacy(self, server: FakeJMAPServer, account: str) -> ContactStore:
        store = ContactStore(server, legacy=True)
        store.add(
            {
                "id": "L1",
                "firstName": "Ada",
                "lastName": "Lovelace",
                "company": "Analytical Society",
                "emails": [{"type": "personal", "value": "ada@example.com"}],
                "phones": [{"type": "mobile", "value": "+44 1"}],
                "addresses": [{"street": "12 St James's Square", "locality": "London"}],
                "notes": "Analyst",
            }
        )
        return store

    def test_list(self, invoke, legacy, server):
        result = invoke("contact", "list", "ada")
        assert rows(result.out) == [
            ["L1", "Ada Lovelace", "ada@example.com", "+44 1", "Analytical Society"]
        ]
        assert server.requests[-1]["methodCalls"][0][0] == "Contact/query"

    def test_show(self, invoke, legacy):
        out = invoke("contact", "show", "ada@example.com").out
        assert "addresses\t12 St James's Square, London" in out
        assert "notes\tAnalyst" in out

    def test_no_books_or_sorting(self, invoke, legacy):
        assert invoke("contact", "list", "--sort", "surname").code == 7

    def test_create_needs_rfc_9610(self, invoke, legacy):
        assert invoke("contact", "create", "--name", "X").code == 7

    def test_delete(self, invoke, legacy):
        assert invoke("contact", "delete", "L1", "--yes").code == 0
        assert not legacy.cards


@pytest.mark.parametrize(
    ("item", "name"),
    [
        ({"name": {"full": "Full"}}, "Full"),
        (
            {
                "name": {
                    "components": [
                        {"kind": "given", "value": "Ada"},
                        {"kind": "separator", "value": " "},
                        {"kind": "surname", "value": "Lovelace"},
                    ]
                }
            },
            "Ada Lovelace",
        ),
        ({"name": {}, "organizations": {"o1": {"name": "Acme"}}}, "Acme"),
        ({"firstName": "Ada"}, "Ada"),
        ({"emails": {"e1": {"address": "a@b.c"}}}, "a@b.c"),
        ({"name": "not an object"}, ""),
    ],
)
def test_display_name(item, name):
    assert display_name(item) == name


def test_odd_values_are_skipped():
    assert emails({"emails": "a@b.c"}) == []
    assert emails({"emails": {"e1": {"address": ""}, "e2": "x"}}) == []
    assert notes({"notes": ""}) == []
    assert organization({}) == ""
    assert addresses(
        {"addresses": {"a1": {"components": [{"kind": "locality", "value": "Paris"}]}}}
    ) == ["Paris"]
