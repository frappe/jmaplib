"""``jmap agent-guide``: the guide for AI agents, printed or saved as a skill."""

from __future__ import annotations

import sys

from jmap.cli.app import run


def test_it_prints_the_guide(invoke):
    out = invoke("agent-guide").out
    assert out.startswith("# Using the jmap command as an agent\n")
    assert "--read-only" in out
    assert "| 11 | Refused: read-only |" in out
    assert "On this machine" not in out


def test_run_as_jmap_cli_it_says_so(make_context, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/jmap-cli", "agent-guide"])
    context = make_context()
    assert run(context=context) == 0
    lines = context.out.file.getvalue().splitlines()
    assert lines[2] == "On this machine the command is `jmap-cli`: type it for every `jmap` below."


def test_install_saves_a_claude_code_skill(invoke, tmp_path):
    result = invoke("agent-guide", "--install", "--skills-dir", str(tmp_path))
    assert result.code == 0, result.err
    skill = tmp_path / "jmap" / "SKILL.md"
    assert f"saved the skill to {skill}" in result.err
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: jmap\ndescription: Read and change")
    assert "# Using the jmap command as an agent" in text


def test_install_keeps_an_existing_skill_unless_forced(invoke, tmp_path):
    invoke("agent-guide", "--install", "--skills-dir", str(tmp_path))
    again = invoke("agent-guide", "--install", "--skills-dir", str(tmp_path))
    assert again.code == 2
    assert "--force" in again.err
    forced = invoke("agent-guide", "--install", "--skills-dir", str(tmp_path), "--force")
    assert forced.code == 0
