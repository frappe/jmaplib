"""``jmap config``: where the file is, editing it, and setting one value."""

from __future__ import annotations

import tomllib
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from jmap.cli.commands import cli
from jmap.cli.config import Config, load_config, save_config
from jmap.cli.editor import editor_command, run_editor
from jmap.cli.errors import ConfigError, UsageError
from jmap.core.narrow import is_object

app: typer.Typer = typer.Typer(no_args_is_help=True)


@app.command()
def path(ctx: typer.Context) -> None:
    """Print where the configuration file is."""
    context = cli(ctx)
    context.out.file.write(f"{context.config_path}\n")


@app.command()
def edit(ctx: typer.Context) -> None:
    """Open the configuration file in your editor, and check it afterwards."""
    context = cli(ctx)
    target = context.config_path
    if not target.exists():
        save_config(Config(), target)
    run_editor(editor_command(context), target)
    load_config(target)  # a mistake is reported now, not on the next command
    context.renderer.note(f"{target} is valid")


@app.command("set")
def set_(
    ctx: typer.Context,
    key: Annotated[
        str, typer.Argument(help="Dotted key: defaults.output, accounts.work.identity.")
    ],
    value: Annotated[str, typer.Argument(help='A TOML value (true, 30, ["a"]) or plain text.')],
) -> None:
    """Set one value in the configuration file: jmap config set defaults.output json."""
    context = cli(ctx)
    data = context.config.model_dump(mode="json")
    parts = key.split(".")
    target = data
    for part in parts[:-1]:
        child = target.get(part)
        if not is_object(child):
            raise UsageError(f"{key!r} is not a setting", hint=_settings_hint())
        target = child
    target[parts[-1]] = _value(value)
    try:
        config = Config.model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ConfigError(f"{where}: {first['msg']}", hint=_settings_hint()) from error
    context.save_config(config)


def _value(text: str) -> Any:
    """``text`` as TOML when it parses as a value, otherwise as the string it is."""
    try:
        return tomllib.loads(f"value = {text}")["value"]
    except tomllib.TOMLDecodeError:
        return text


def _settings_hint() -> str:
    return "see 'Configuration' in docs/cli.md for the keys"
