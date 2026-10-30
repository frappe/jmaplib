"""The command surface, pinned like the library's public API.

Nouns and verbs may be added freely; removing or renaming one breaks scripts,
so it must fail here first. No command may define an option that a global
option already owns, or ``run`` would move it away from the command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from jmap.cli.app import RESERVED, app

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Every noun and its verbs. Removing one is a breaking change.
SURFACE = {
    "account": {"add", "list", "show", "remove", "set-default", "test", "login"},
    "mailbox": {"list", "show"},
    "server": {"session", "capabilities", "conformance", "echo", "refresh"},
    "api": {"call"},
}


def tree() -> typer.core.TyperGroup:
    command = typer.main.get_command(app)
    assert isinstance(command, typer.core.TyperGroup)
    return command


def walk(group: typer.core.TyperGroup) -> Iterator[tuple[str, object]]:
    for name, command in group.commands.items():
        yield name, command
        if isinstance(command, typer.core.TyperGroup):
            yield from ((f"{name} {sub}", inner) for sub, inner in walk(command))


def test_every_noun_and_verb_exists():
    root = tree()
    for noun, verbs in SURFACE.items():
        group = root.commands[noun]
        assert isinstance(group, typer.core.TyperGroup), noun
        assert set(group.commands) >= verbs, noun


def test_no_command_reuses_a_global_option():
    for path, command in walk(tree()):
        for param in command.params:  # type: ignore[attr-defined]
            names = set(param.opts) | set(param.secondary_opts)
            assert not names & RESERVED, f"{path}: {sorted(names & RESERVED)}"


def test_every_global_option_is_defined_on_the_root():
    names = {name for param in tree().params for name in (*param.opts, *param.secondary_opts)}
    assert names >= RESERVED
