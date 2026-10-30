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
    "mailbox": {
        "list",
        "show",
        "create",
        "rename",
        "move",
        "delete",
        "subscribe",
        "unsubscribe",
        "empty",
    },
    "mail": {
        "list",
        "search",
        "read",
        "thread",
        "move",
        "copy",
        "archive",
        "delete",
        "flag",
        "unflag",
        "seen",
        "unseen",
        "junk",
        "notjunk",
        "keyword",
        "import",
        "export",
    },
    "server": {"session", "capabilities", "conformance", "echo", "refresh"},
    "api": {"call", "batch"},
    "config": {"path", "edit", "set"},
    "draft": {"list", "new", "edit", "send", "delete"},
    "attachment": {"list", "download"},
    "identity": {"list", "show", "create", "update", "delete"},
    "submission": {"list", "show", "cancel"},
    "vacation": {"show", "set", "enable", "disable"},
    "addressbook": {"list", "create", "rename", "delete"},
    "contact": {"list", "show", "create", "edit", "delete", "import"},
    "principal": {"list", "show", "me"},
    "share": {"list", "grant", "revoke", "unshare", "subscribe", "unsubscribe", "notifications"},
    "mdn": {"send", "parse"},
    "blob": {"upload", "download", "info", "lookup", "copy"},
    "sieve": {"list", "get", "put", "validate", "activate", "deactivate", "delete"},
    "quota": {"list", "show"},
    "push": {"list", "add", "verify", "renew", "remove", "keys", "decode"},
    "calendar": {"list", "create", "rename", "delete"},
    "event": {"list", "show", "create", "import", "update", "delete", "notifications"},
    "file": {"ls", "tree", "upload", "download", "mkdir", "rm", "mv"},
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


#: Top-level shortcuts for the daily verbs.
SHORTCUTS = {
    "ls",
    "search",
    "read",
    "thread",
    "move",
    "delete",
    "archive",
    "flag",
    "unflag",
    "seen",
    "unseen",
}


def test_every_shortcut_exists_and_is_the_same_command():
    root = tree()
    mail = root.commands["mail"]
    assert isinstance(mail, typer.core.TyperGroup)
    assert set(root.commands) >= SHORTCUTS
    # Typer wraps each callback, so compare what the commands accept.
    for alias, verb in (("ls", "list"), ("read", "read"), ("move", "move")):
        alias_params = [param.name for param in root.commands[alias].params]
        assert alias_params == [param.name for param in mail.commands[verb].params], alias


def test_the_top_level_verbs_exist():
    assert {"shell", "compose", "send", "reply", "forward"} <= set(tree().commands)
    assert {"changes", "watch", "freebusy", "completion"} <= set(tree().commands)


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
