"""Turning results into tables for people and JSON for programs.

The JSON contract is stable and documented: stdout holds only the server's wire
objects, as the library's ``to_wire()`` produces them. A list is a bare JSON
array; anything about the query itself (totals, positions) goes to stderr as a
note. Tables and plain text are for reading and may change between releases.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from rich import box
from rich.markup import escape
from rich.table import Table

from jmap.core.narrow import is_list, is_object
from jmap.models.base import JMAPObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rich.console import Console, JustifyMethod


class OutputFormat(StrEnum):
    AUTO = "auto"
    TABLE = "table"
    PLAIN = "plain"
    JSON = "json"
    JSONL = "jsonl"
    IDS = "ids"


class ColorMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class OutputOptions:
    format: OutputFormat = OutputFormat.AUTO
    quiet: bool = False
    #: The command to page tall tables through; empty means no paging.
    pager: str = ""

    @property
    def json_mode(self) -> bool:
        return self.format in {OutputFormat.JSON, OutputFormat.JSONL}


@dataclass(frozen=True, slots=True)
class Column:
    """One column: a header and how to get its text out of an item."""

    header: str
    key: str
    #: Turns the item into the cell's text; by default, ``str(item[key])``.
    text: Callable[[Mapping[str, Any]], str] | None = None
    justify: JustifyMethod = "left"

    def cell(self, item: Mapping[str, Any]) -> str:
        if self.text is not None:
            return self.text(item)
        value = item.get(self.key)
        return "" if value is None else str(value)


@dataclass(frozen=True, slots=True)
class View:
    """Something to show: wire items and the columns a person sees of them."""

    items: Sequence[Mapping[str, Any]]
    columns: Sequence[Column]
    #: One object rather than a list: JSON is an object, a table is key/value rows.
    single: bool = False
    title: str | None = None
    footer: str | None = field(default=None)


class Renderer:
    """Writes views to stdout and notes to stderr, in the chosen format."""

    def __init__(self, out: Console, err: Console, options: OutputOptions) -> None:
        self.out = out
        self.err = err
        self.options = options

    @property
    def format(self) -> OutputFormat:
        chosen = self.options.format
        if chosen is OutputFormat.AUTO:
            return OutputFormat.TABLE if self.out.is_terminal else OutputFormat.PLAIN
        return chosen

    def render(self, view: View) -> None:
        handlers = {
            OutputFormat.TABLE: self._table,
            OutputFormat.PLAIN: self._plain,
            OutputFormat.JSON: self._json,
            OutputFormat.JSONL: self._jsonl,
            OutputFormat.IDS: self._ids,
        }
        handlers[self.format](view)
        if view.footer and not self.options.json_mode:
            self.note(view.footer)

    def json(self, value: Any) -> None:
        """Write one JSON value, whatever the format: for commands that only speak JSON."""
        self._write(json.dumps(wire_of(value), indent=2, ensure_ascii=False))

    def note(self, text: str) -> None:
        if not self.options.quiet:
            self.err.print(escape(text), style="dim", highlight=False)

    def _table(self, view: View) -> None:
        table = _key_value_table(view) if view.single else _list_table(view)
        if self.options.pager and self.out.is_terminal and _rows(view) > self.out.height:
            with self.out.capture() as captured:
                self.out.print(table)
            _page(self.options.pager, captured.get())
            return
        self.out.print(table)

    def _plain(self, view: View) -> None:
        if view.single:
            for item in view.items:
                for column in view.columns:
                    self._write(f"{column.header.lower()}\t{_flat(column.cell(item))}")
            return
        for item in view.items:
            self._write("\t".join(_flat(column.cell(item)) for column in view.columns))

    def _json(self, view: View) -> None:
        items = [wire_of(item) for item in view.items]
        self.json(items[0] if view.single and items else items)

    def _jsonl(self, view: View) -> None:
        for item in view.items:
            self._write(json.dumps(wire_of(item), ensure_ascii=False))

    def _ids(self, view: View) -> None:
        for item in view.items:
            if item.get("id") is not None:
                self._write(str(item["id"]))

    def _write(self, line: str) -> None:
        # Data goes to the file directly: rich would wrap it and read markup in it.
        self.out.file.write(line + "\n")


def wire_of(value: Any) -> Any:
    """The JSON shape of a library value: models and objects as their wire form."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, JMAPObject):
        return value.raw
    if is_object(value):
        return {key: wire_of(item) for key, item in value.items()}
    if is_list(value):
        return [wire_of(item) for item in value]
    return value


def _list_table(view: View) -> Table:
    table = Table(box=box.SIMPLE_HEAD, title=view.title, pad_edge=False)
    for column in view.columns:
        table.add_column(column.header, justify=column.justify, overflow="fold")
    for item in view.items:
        table.add_row(*(escape(column.cell(item)) for column in view.columns))
    return table


def _key_value_table(view: View) -> Table:
    table = Table(box=None, show_header=False, title=view.title, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    for item in view.items:
        for column in view.columns:
            table.add_row(column.header, escape(column.cell(item)))
    return table


def _rows(view: View) -> int:
    return len(view.items) * (len(view.columns) if view.single else 1) + 4


def _flat(text: str) -> str:
    return text.replace("\t", " ").replace("\n", " ")


def _page(command: str, text: str) -> None:
    subprocess.run(shlex.split(command), input=text, text=True, check=False)
