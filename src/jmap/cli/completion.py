"""The shell's line editor: prompt_toolkit with history and Tab completion.

Imported only when prompt_toolkit is installed; :mod:`jmap.cli.shell` falls back
to plain line reading without it. What to complete is decided there; this module
only adapts it to prompt_toolkit.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from prompt_toolkit.input import Input
    from prompt_toolkit.output import Output


class ShellCompleter(Completer):
    """Completes the word under the cursor from the shell's candidates."""

    def __init__(self, candidates: Callable[[list[str], str], list[str]]) -> None:
        self._candidates = candidates

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        try:
            words = shlex.split(text)
        except ValueError:
            return  # inside an open quote: nothing sensible to offer
        current = "" if not words or text[-1:].isspace() else words.pop()
        for candidate in self._candidates(words, current):
            if candidate.startswith(current):
                yield Completion(candidate, start_position=-len(current))


class PromptToolkitLines:
    """Lines from a prompt_toolkit session, with history in ``history``."""

    def __init__(
        self,
        history: Path,
        candidates: Callable[[list[str], str], list[str]],
        *,
        input: Input | None = None,  # the names prompt_toolkit uses
        output: Output | None = None,
    ) -> None:
        history.parent.mkdir(parents=True, exist_ok=True)
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history)),
            completer=ShellCompleter(candidates),
            input=input,
            output=output,
        )

    def read(self, prompt: str) -> str:
        return self._session.prompt(prompt)
