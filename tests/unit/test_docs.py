"""The documentation must describe the library that exists.

Prose goes stale quietly. An example naming a method that was renamed, or a
keyword argument that never existed, reads exactly like one that works - and the
reader finds out at run time, which is the worst possible place. So the parts of
the docs that make checkable claims are checked here.

Three claims are worth enforcing, in rising order of what they catch:

* every Python block **parses**, so no example is malformed;
* every ``from jmap... import X`` **resolves**, so no example imports a symbol
  that is gone;
* every ``batch.<capability>.<entity>.<method>(`` **exists in the registry**,
  which is the one that catches a documented method the library cannot perform;
* every ``$ jmap ...`` line in a console block names a **real command** with
  real options, and ``docs/cli-reference.md`` is what the command tree generates.

What this deliberately does not do is execute the examples. Most of them need a
server, and the ones that do not would need so much scaffolding that the
scaffolding would become the thing under test. The three checks above catch
staleness, which is the failure mode that actually happens.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import typer
import typer.core
import typer.main

from jmap.api.namespace import attribute_name
from jmap.cli.app import app
from jmap.defaults import default_registry

if TYPE_CHECKING:
    from collections.abc import Iterator

DOCS = Path(__file__).parents[2] / "docs"
README = Path(__file__).parents[2] / "README.md"
SCRIPTS = Path(__file__).parents[2] / "scripts"

#: ```python fenced blocks, with their starting line so a failure can be found.
_BLOCK = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)

#: `batch.mail.email.get(` and friends.
_CALL = re.compile(r"\bbatch\.([a-z_]+)\.([a-z_]+)\.([a-z_]+)\(")

#: A method that exists but is reached through `batch.add` rather than a builder.
_ADD = re.compile(r"\bbatch\.add\(\s*[\"']([A-Za-z]+/[A-Za-z]+)[\"']")

#: ```console fenced blocks, where `$ jmap ...` lines are commands to check.
_CONSOLE = re.compile(r"^```console\n(.*?)^```", re.DOTALL | re.MULTILINE)

#: `JMAP_URL=... jmap ...`: an environment assignment before the command.
_ENVIRONMENT = re.compile(r"[A-Z_][A-Z0-9_]*=")


#: The guide ``jmap agent-guide`` prints, which ships inside the package.
AGENT_GUIDE = Path(__file__).parents[2] / "src" / "jmap" / "cli" / "agent_guide.md"


def markdown_files() -> list[Path]:
    return [*sorted(DOCS.glob("*.md")), README, AGENT_GUIDE]


def python_blocks(path: Path) -> Iterator[tuple[int, str]]:
    text = path.read_text()
    for match in _BLOCK.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        yield line, match.group(1)


def registry_surface() -> tuple[dict[str, dict[str, set[str]]], set[str]]:
    """``{capability_attr: {entity_attr: {method, ...}}}`` and every method name."""
    surface: dict[str, dict[str, set[str]]] = {}
    method_names: set[str] = set()
    registry = default_registry()
    specs = [spec for urn in registry.urns for spec in registry.specs_for(urn)]
    # A capability with no attribute lends its methods to the namespace that
    # holds their data type, as the client's Namespaces does.
    lent = [method for spec in specs if spec.attr is None for method in spec.methods]
    for spec in specs:
        for method in spec.methods:
            method_names.add(method.name)
        if spec.attr is None:
            continue
        entities = surface.setdefault(spec.attr, {})
        for data_type in spec.data_types:
            suffixes = {
                method.name.split("/", 1)[1]
                for method in (*spec.methods, *lent)
                if method.name.split("/", 1)[0] == data_type.name
            }
            if suffixes:
                entities[attribute_name(data_type.name)] = suffixes
    return surface, method_names


SURFACE, METHOD_NAMES = registry_surface()

#: Builders whose Python name differs from the wire suffix, or that a mixin adds
#: without the registry naming a separate method.
_ALIASES = {
    "query_changes": "queryChanges",
    # `import` is a keyword.
    "import_": "import",
    "get_availability": "getAvailability",
    # SieveScript/set drives both, through onSuccessActivateScript.
    "activate": "set",
    "deactivate": "set",
}


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.name)
class TestEveryDocument:
    def test_python_blocks_parse(self, path: Path) -> None:
        for line, block in python_blocks(path):
            # `...` stands in for elided code and is a valid expression, so a
            # block using it still parses.
            try:
                ast.parse(block)
            except SyntaxError as error:  # pragma: no cover - only on a bad doc
                pytest.fail(f"{path.name}:{line} does not parse: {error}")

    def test_imports_resolve(self, path: Path) -> None:
        for line, block in python_blocks(path):
            for node in ast.walk(ast.parse(block)):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = node.module or ""
                if not module.startswith("jmap"):
                    continue
                imported = __import__(module, fromlist=["_"])
                for alias in node.names:
                    if not hasattr(imported, alias.name):  # pragma: no cover
                        pytest.fail(
                            f"{path.name}:{line} imports {alias.name!r} from "
                            f"{module}, which does not export it"
                        )

    def test_documented_calls_exist(self, path: Path) -> None:
        text = path.read_text()
        for capability, entity, method in _CALL.findall(text):
            entities = SURFACE.get(capability)
            if entities is None:  # pragma: no cover - only on a bad doc
                pytest.fail(f"{path.name}: no capability exposes batch.{capability}")
            suffixes = entities.get(entity)
            if suffixes is None:  # pragma: no cover
                pytest.fail(f"{path.name}: batch.{capability} has no {entity!r}")
            wanted = _ALIASES.get(method, method)
            if wanted not in suffixes:  # pragma: no cover
                pytest.fail(
                    f"{path.name}: batch.{capability}.{entity} has no {method!r} "
                    f"(it has {sorted(suffixes)})"
                )

    def test_raw_calls_name_a_real_method(self, path: Path) -> None:
        for name in _ADD.findall(path.read_text()):
            if name not in METHOD_NAMES:  # pragma: no cover - only on a bad doc
                pytest.fail(f"{path.name}: batch.add({name!r}) names no modelled method")

    def test_documented_commands_exist(self, path: Path) -> None:
        for words in jmap_commands(path):
            problem = TREE.check(words[1:])
            assert problem is None, f"{path.name}: {shlex.join(words)}: {problem}"


class TestTheReadmeIsCurrent:
    def test_the_status_names_this_release(self):
        # It said 1.0.0 through the 1.1.0 release.
        import jmap

        assert f"**{jmap.__version__}**" in README.read_text()

    def test_it_does_not_call_shipped_features_future_work(self):
        # "Interactive OAuth acquisition ... lands after 0.1.0; for now you
        # bring a token" - long after it shipped.
        assert "for now you bring a token" not in README.read_text()


class TestTheDocumentationIsWired:
    def test_every_guide_is_linked_from_the_index(self):
        index = (DOCS / "index.md").read_text()
        for path in DOCS.glob("*.md"):
            if path.name == "index.md":
                continue
            assert path.name in index, f"{path.name} is not linked from docs/index.md"

    def test_internal_links_point_at_something(self):
        link = re.compile(r"\]\(([a-z0-9-]+\.md)(#[a-z0-9-]+)?\)")
        for path in markdown_files():
            for target, _anchor in link.findall(path.read_text()):
                assert (DOCS / target).exists(), f"{path.name} links to missing {target}"


class TestTheCommandCheck:
    """The check above is only as good as what it extracts and rejects."""

    def test_it_reads_continued_lines_and_stops_at_a_pipe(self):
        commands = list(jmap_commands(README))
        assert ["jmap", "ls", "--json"] in commands
        assert any(words[:3] == ["jmap", "account", "add"] for words in commands)

    def test_it_drops_environment_assignments_and_comments(self):
        commands = list(jmap_commands(DOCS / "cli.md"))
        assert ["jmap", "server", "capabilities"] in commands
        assert ["jmap", "compose"] in commands

    @pytest.mark.parametrize(
        "words",
        [["ls", "--sort", "-date"], ["-a", "bob", "ls"], ["mail", "list", "--json"], ["quota"]],
    )
    def test_it_accepts_real_command_lines(self, words):
        assert TREE.check(words) is None

    @pytest.mark.parametrize(
        ("words", "problem"),
        [
            (["mail", "nope"], "jmap mail has no command 'nope'"),
            (["ls", "--nope"], "jmap ls has no option --nope"),
            (["--nope", "ls"], "jmap has no option --nope"),
        ],
    )
    def test_it_rejects_what_does_not_exist(self, words, problem):
        assert TREE.check(words) == problem


class TestTheCommandReference:
    def test_it_is_what_the_command_tree_generates(self):
        expected = load_script("cli-reference.py").render()
        page = (DOCS / "cli-reference.md").read_text(encoding="utf-8")
        assert page == expected, "stale: run uv run python scripts/cli-reference.py"


def jmap_commands(path: Path) -> Iterator[list[str]]:
    """The words of each ``$ jmap ...`` line in the console blocks of ``path``."""
    for match in _CONSOLE.finditer(path.read_text()):
        for line in match.group(1).replace("\\\n", " ").splitlines():
            if line.startswith("$ "):
                words = shell_words(line[2:])
                if words[:1] == ["jmap"]:
                    yield words


def shell_words(line: str) -> list[str]:
    """The words of the first command on ``line``, without its environment."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words: list[str] = []
    for word in lexer:
        if set(word) <= set(lexer.punctuation_chars):
            break
        if words or not _ENVIRONMENT.match(word):
            words.append(word)
    return words


class CommandTree:
    """Checks a command line against the real command tree, without running it."""

    def __init__(self) -> None:
        root = typer.main.get_command(app)
        assert isinstance(root, typer.core.TyperGroup)
        self.root = root
        self.globals = {"-h": False, "--help": False, **options_of(root)}

    def check(self, words: list[str]) -> str | None:
        """Why ``words``, the line after ``jmap``, is not a valid command, or None."""
        command: Any = self.root
        context = typer.Context(command, info_name="jmap")
        path, known = ["jmap"], self.globals
        index = 0
        while index < len(words) and words[index] != "--":
            word = words[index]
            index += 1
            if word.startswith("-") and word != "-":
                name = word.partition("=")[0]
                if name not in known:
                    return f"{' '.join(path)} has no option {name}"
                if known[name] and "=" not in word:
                    index += 1  # its value, which may itself start with "-"
            elif isinstance(command, typer.core.TyperGroup):
                command = command.get_command(context, word)
                if command is None:
                    return f"{' '.join(path)} has no command {word!r}"
                context = typer.Context(command, info_name=word, parent=context)
                path.append(word)
                known = {**self.globals, **options_of(command)}
        return None


def options_of(command: Any) -> dict[str, bool]:
    """Each option name of ``command``, and whether a value follows it."""
    return {
        name: not (param.is_flag or param.count)
        for param in command.params
        if isinstance(param, typer.core.TyperOption)
        for name in (*param.opts, *param.secondary_opts)
    }


def load_script(name: str) -> Any:
    """A module from ``scripts/``, whose file names are not importable names."""
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), SCRIPTS / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TREE = CommandTree()
