"""``jmap sieve``: the server-side mail filters (RFC 9661).

A script's text is a blob. ``put`` uploads it first, then checks it with
``SieveScript/validate`` by its real blob id: Stalwart does not resolve a
``#creationId`` there. At most one script is active; activation is an argument
of ``SieveScript/set``, not a property to patch.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.commands.blob import read_source
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.files import write_file
from jmap.cli.output import Column, View, wire_of

if TYPE_CHECKING:
    from jmap.client import JMAPClient
    from jmap.core.errors import SetError

app: typer.Typer = typer.Typer(no_args_is_help=True)

Script = Annotated[str, typer.Argument(help="The script, by name or id.")]
Source = Annotated[str, typer.Argument(help="A Sieve file, or - for stdin.")]

_TYPE = "application/sieve"
_COLUMNS = [
    Column("ID", "id"),
    Column("NAME", "name"),
    Column("ACTIVE", "isActive", lambda i: "yes" if i.get("isActive") else ""),
    Column("BLOB", "blobId"),
]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List the scripts; at most one is active."""
    context = cli(ctx)
    context.renderer.render(View(_scripts(context.require("SieveScript/get")), _COLUMNS))


@app.command()
def get(
    ctx: typer.Context,
    script: Script,
    save: Annotated[str | None, typer.Option(help="Save to this file instead.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace a file that exists.")] = False,
) -> None:
    """Print a script's text."""
    context = cli(ctx)
    client = context.require("SieveScript/get")
    found = _find(_scripts(client), script)
    data = client.download(str(found["blobId"]), name=f"{found['name']}.sieve", content_type=_TYPE)
    if save is None or save == "-":
        context.write_bytes(data)
        return
    write_file(Path(save), data, force=force)
    context.renderer.note(f"saved {save}")


@app.command()
def put(
    ctx: typer.Context,
    source: Source,
    name: Annotated[
        str | None, typer.Option(help="The script's name; the file's name if not given.")
    ] = None,
    activate: Annotated[bool, typer.Option("--activate", help="Make it the active one.")] = False,
    validate: Annotated[
        bool, typer.Option("--validate/--no-validate", help="Check it before storing it.")
    ] = True,
) -> None:
    """Store a script, replacing the one of the same name."""
    context = cli(ctx)
    if name is None and source == "-":
        raise UsageError("a script from stdin needs --name")
    title = name or Path(source).stem
    client = context.require("SieveScript/get", "SieveScript/set", "SieveScript/validate")
    blob_id = str(client.upload(read_source(context, source), content_type=_TYPE).blob_id)
    with client.batch() as batch:
        listed = batch.sieve.sieve_script.get(ids=None)
        checked = batch.sieve.sieve_script.validate(blob_id=blob_id) if validate else None
    if checked is not None:
        _check(checked.result.problem)
    existing = [item for item in wire_of(listed.result)["list"] if item.get("name") == title]
    with client.batch() as batch:
        if existing:
            target = str(existing[0]["id"])
            stored = batch.sieve.sieve_script.set(update={target: {"blobId": blob_id}})
        else:
            target = "#script"
            stored = batch.sieve.sieve_script.set(
                create={"script": {"name": title, "blobId": blob_id}}
            )
        activated = batch.sieve.sieve_script.activate(target) if activate else None
    report_set(context, stored.result, f"stored the script {title}")
    if activated is not None:
        report_set(context, activated.result, f"{title} is the active script")


@app.command()
def validate(ctx: typer.Context, source: Source) -> None:
    """Check a script without storing it; exits 2 if it is not valid."""
    context = cli(ctx)
    client = context.require("SieveScript/validate")
    blob_id = str(client.upload(read_source(context, source), content_type=_TYPE).blob_id)
    with client.batch() as batch:
        checked = batch.sieve.sieve_script.validate(blob_id=blob_id)
    if context.options.json_mode:
        context.renderer.json(checked.result)
    _check(checked.result.problem)
    context.renderer.note("the script is valid")


@app.command()
def activate(ctx: typer.Context, script: Script) -> None:
    """Make a script the active one."""
    context = cli(ctx)
    client = context.require("SieveScript/get", "SieveScript/set")
    found = _find(_scripts(client), script)
    with client.batch() as batch:
        handle = batch.sieve.sieve_script.activate(str(found["id"]))
    report_set(context, handle.result, f"{found['name']} is the active script")


@app.command()
def deactivate(ctx: typer.Context) -> None:
    """Turn filtering off: no script is active after this."""
    context = cli(ctx)
    client = context.require("SieveScript/set")
    with client.batch() as batch:
        handle = batch.sieve.sieve_script.deactivate()
    report_set(context, handle.result, "no script is active")


@app.command()
def delete(ctx: typer.Context, script: Script) -> None:
    """Delete a script, after you confirm; an active one is deactivated first."""
    context = cli(ctx)
    client = context.require("SieveScript/get", "SieveScript/set")
    found = _find(_scripts(client), script)
    context.confirm(f"Delete the script {found['name']}?")
    with client.batch() as batch:
        # RFC 9661 §2.4: the deactivation must be its own /set, before the destroy.
        if found.get("isActive"):
            batch.sieve.sieve_script.deactivate()
        handle = batch.sieve.sieve_script.set(destroy=[found["id"]])
    report_set(context, handle.result, f"deleted the script {found['name']}")


def _scripts(client: JMAPClient) -> list[dict[str, Any]]:
    with client.batch() as batch:
        handle = batch.sieve.sieve_script.get(ids=None)
    scripts: list[dict[str, Any]] = wire_of(handle.result)["list"]
    return scripts


def _find(scripts: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    for item in scripts:
        if wanted in (item.get("id"), item.get("name")):
            return item
    raise NotFoundError(f"no script {wanted!r}", hint="see 'jmap sieve list'")


def _check(problem: SetError | None) -> None:
    """Raise for a script the server called invalid, with its own explanation."""
    if problem is not None:
        raise UsageError(f"the script is not valid: {problem.description or problem.type}")
