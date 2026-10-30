"""Rendering: tables for people, the stable JSON contract for programs."""

from __future__ import annotations

import io
import json
import subprocess
from typing import Any

from rich.console import Console

from jmap.cli.output import Column, OutputFormat, OutputOptions, Renderer, View, wire_of
from jmap.models.base import JMAPObject
from jmap.models.mail.objects import Mailbox

ITEMS: list[dict[str, Any]] = [
    {"id": "m1", "name": "Inbox", "n": 3},
    {"id": "m2", "name": "a\tb\nc", "n": None},
]
COLUMNS = [Column("NAME", "name"), Column("N", "n", justify="right")]


def renderer(
    fmt: OutputFormat = OutputFormat.AUTO,
    *,
    terminal: bool = False,
    quiet: bool = False,
    pager: str = "",
    height: int | None = None,
) -> tuple[Renderer, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    console = Console(file=out, force_terminal=terminal, width=80, height=height)
    options = OutputOptions(format=fmt, quiet=quiet, pager=pager)
    return Renderer(console, Console(file=err, width=80), options), out, err


class TestFormats:
    def test_auto_is_a_table_on_a_terminal_and_plain_elsewhere(self):
        assert renderer(terminal=True)[0].format is OutputFormat.TABLE
        assert renderer(terminal=False)[0].format is OutputFormat.PLAIN

    def test_plain_is_tab_separated_and_flat(self):
        r, out, _ = renderer(OutputFormat.PLAIN)
        r.render(View(ITEMS, COLUMNS))
        assert out.getvalue() == "Inbox\t3\na b c\t\n"

    def test_plain_single_is_key_value(self):
        r, out, _ = renderer(OutputFormat.PLAIN)
        r.render(View(ITEMS[:1], COLUMNS, single=True))
        assert out.getvalue() == "name\tInbox\nn\t3\n"

    def test_table(self):
        r, out, _ = renderer(OutputFormat.TABLE)
        r.render(View(ITEMS, COLUMNS, title="Boxes"))
        text = out.getvalue()
        assert "Boxes" in text
        assert "NAME" in text
        assert "Inbox" in text

    def test_table_single(self):
        r, out, _ = renderer(OutputFormat.TABLE)
        r.render(View(ITEMS[:1], COLUMNS, single=True))
        assert "NAME" in out.getvalue()
        assert "Inbox" in out.getvalue()

    def test_table_cells_are_not_markup(self):
        r, out, _ = renderer(OutputFormat.TABLE)
        r.render(View([{"name": "[red]x"}], [Column("NAME", "name")]))
        assert "[red]x" in out.getvalue()

    def test_json_is_a_bare_array_of_wire_objects(self):
        r, out, _ = renderer(OutputFormat.JSON)
        r.render(View(ITEMS, COLUMNS, footer="2 of 2"))
        assert json.loads(out.getvalue()) == ITEMS

    def test_json_single_is_one_object(self):
        r, out, _ = renderer(OutputFormat.JSON)
        r.render(View(ITEMS[:1], COLUMNS, single=True))
        assert json.loads(out.getvalue()) == ITEMS[0]
        r, out, _ = renderer(OutputFormat.JSON)
        r.render(View([], COLUMNS, single=True))
        assert json.loads(out.getvalue()) == []

    def test_jsonl_is_one_object_per_line(self):
        r, out, _ = renderer(OutputFormat.JSONL)
        r.render(View(ITEMS, COLUMNS))
        assert [json.loads(line) for line in out.getvalue().splitlines()] == ITEMS

    def test_ids(self):
        r, out, _ = renderer(OutputFormat.IDS)
        r.render(View([*ITEMS, {"name": "no id"}], COLUMNS))
        assert out.getvalue() == "m1\nm2\n"


class TestNotes:
    def test_the_footer_goes_to_stderr(self):
        r, out, err = renderer(OutputFormat.PLAIN)
        r.render(View(ITEMS, COLUMNS, footer="2 of 2"))
        assert "2 of 2" not in out.getvalue()
        assert "2 of 2" in err.getvalue()

    def test_json_modes_have_no_footer(self):
        r, _, err = renderer(OutputFormat.JSONL)
        r.render(View(ITEMS, COLUMNS, footer="2 of 2"))
        assert err.getvalue() == ""

    def test_quiet(self):
        r, _, err = renderer(quiet=True)
        r.note("hello")
        assert err.getvalue() == ""


class TestPager:
    def test_a_tall_table_on_a_terminal_is_paged(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append((args, kw)))
        r, out, _ = renderer(OutputFormat.TABLE, terminal=True, pager="less -R", height=3)
        r.render(View(ITEMS, COLUMNS))
        assert out.getvalue() == ""
        assert calls[0][0] == ["less", "-R"]
        assert "Inbox" in calls[0][1]["input"]

    def test_a_short_table_is_not(self, monkeypatch):
        r, out, _ = renderer(OutputFormat.TABLE, terminal=True, pager="less", height=100)
        r.render(View(ITEMS[:1], COLUMNS, single=True))
        assert "Inbox" in out.getvalue()


class TestWire:
    def test_models_use_their_wire_names(self):
        box = Mailbox.from_wire({"id": "m1", "parentId": None, "x-extra": 1})
        assert wire_of(box) == {"id": "m1", "parentId": None, "x-extra": 1}

    def test_objects_containers_and_scalars(self):
        assert wire_of(JMAPObject({"a": 1})) == {"a": 1}
        assert wire_of({"k": [Mailbox(name="x"), 2]}) == {"k": [{"name": "x"}, 2]}
        assert wire_of("s") == "s"

    def test_json_writes_any_value(self):
        r, out, _ = renderer(OutputFormat.TABLE)
        r.json({"a": "é"})
        assert out.getvalue() == '{\n  "a": "é"\n}\n'


class TestNumbered:
    def test_plain_rows_carry_their_number(self):
        r, out, _ = renderer(OutputFormat.PLAIN)
        r.render(View(ITEMS, COLUMNS, first_number=26))
        assert out.getvalue().splitlines()[1].startswith("27\t")

    def test_table_has_a_number_column_and_markup_cells(self):
        r, out, _ = renderer(OutputFormat.TABLE)
        marked = Column("NAME", "name", markup=lambda item: f"[bold]{item['name']}[/]")
        r.render(View(ITEMS[:1], [marked], first_number=1))
        assert "#" in out.getvalue()
        assert "[bold]" not in out.getvalue()

    def test_json_has_no_numbers(self):
        r, out, _ = renderer(OutputFormat.JSON)
        r.render(View(ITEMS, COLUMNS, first_number=1))
        assert json.loads(out.getvalue()) == ITEMS
