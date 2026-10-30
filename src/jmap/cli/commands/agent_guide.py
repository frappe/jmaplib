"""``jmap agent-guide``: how an AI agent should use this command.

The guide ships inside the package, so every install has the one that matches
its commands. An agent reads it with one command; ``--install`` saves it as a
Claude Code skill, which Claude Code loads by itself when mail comes up.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Annotated

import typer

from jmap.cli.commands import cli
from jmap.cli.files import write_file

#: Where Claude Code looks for a person's own skills.
SKILLS: Path = Path("~/.claude/skills")


def agent_guide(
    ctx: typer.Context,
    install: Annotated[
        bool, typer.Option("--install", help="Save it as a Claude Code skill.")
    ] = False,
    skills: Annotated[Path, typer.Option("--skills-dir", help="Where to save the skill.")] = SKILLS,
    force: Annotated[bool, typer.Option("--force", help="Replace a saved skill.")] = False,
) -> None:
    """Print the guide for AI agents: safe use, searches, output and exit codes.

    --install saves it as a Claude Code skill, in ~/.claude/skills/jmap/SKILL.md
    (jmap-cli/ when run as jmap-cli).
    """
    context = cli(ctx)
    program = ctx.find_root().info_name or "jmap"
    text = guide(program)
    if not install:
        context.out.file.write(text)
        return
    path = skills.expanduser() / program / "SKILL.md"
    write_file(path, (_front_matter(program) + text).encode(), force=force)
    context.renderer.note(f"saved the skill to {path}; new Claude Code sessions load it")


def guide(program: str) -> str:
    """The guide, telling the agent to type ``program`` where it says ``jmap``."""
    text = resources.files("jmap.cli").joinpath("agent_guide.md").read_text(encoding="utf-8")
    if program == "jmap":
        return text
    title, rest = text.split("\n", 1)
    note = f"On this machine the command is `{program}`: type it for every `jmap` below."
    return f"{title}\n\n{note}\n{rest}"


def _front_matter(program: str) -> str:
    return (
        "---\n"
        f"name: {program}\n"
        f"description: Read and change the person's email, contacts, calendars and files "
        f"with the {program} command (JMAP). Use it when they ask about their mail - counts, "
        "searches, reading, sending, filing - or their contacts, calendar or files.\n"
        "---\n\n"
    )
