"""``jmap api``: call any modelled method with raw JSON arguments.

``call`` makes one call; ``batch`` sends a file of calls as one request.

The library's gates still apply: a method this build does not model, or one the
server does not advertise, is refused before anything is sent. ``accountId`` is
filled in from the selected JMAP account unless the arguments give one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

import typer

from jmap.cli.commands import cli, json_argument
from jmap.cli.errors import UsageError
from jmap.cli.output import wire_of
from jmap.core.errors import MethodError

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext
    from jmap.core.invocation import Handle

app: typer.Typer = typer.Typer(no_args_is_help=True)

Using = Annotated[
    list[str] | None, typer.Option(help="Extra capability URN for 'using' (repeatable).")
]

#: One call from a batch file: method name, arguments, call id.
Call = tuple[str, dict[str, Any], str]


@app.command()
def call(
    ctx: typer.Context,
    method: Annotated[str, typer.Argument(help="Method name, such as Mailbox/get.")],
    arguments: Annotated[
        str | None, typer.Argument(help="JSON object of arguments, @FILE, or - for stdin.")
    ] = None,
    using: Using = None,
) -> None:
    """Make one method call and print its response arguments as JSON."""
    context = cli(ctx)
    args = json_argument(arguments, context)
    client = context.require(method)
    with client.batch(extra_using=frozenset(using or ())) as batch:
        handle = batch.add(method, args)
    context.renderer.json(handle.result)


@app.command()
def batch(
    ctx: typer.Context,
    source: Annotated[
        str, typer.Argument(help="A JSON array of [method, arguments, callId], or - for stdin.")
    ],
    using: Using = None,
) -> None:
    """Make several calls in one request and print the responses as JSON.

    An argument '#ids': {"resultOf": "q", "path": "/ids"} takes its value from the
    result of the earlier call whose call id in this file is "q".
    """
    context = cli(ctx)
    calls = _calls(context, source)
    client = context.require(*{name for name, _, _ in calls})
    handles: dict[str, Handle[Any]] = {}
    with client.batch(extra_using=frozenset(using or ())) as queued:
        for name, arguments, call_id in calls:
            handles[call_id] = queued.add(name, _references(arguments, handles))
    responses: list[list[Any]] = []
    failed: MethodError | None = None
    for name, _, call_id in calls:
        try:
            responses.append([name, wire_of(handles[call_id].result), call_id])
        except MethodError as error:
            responses.append(["error", {"type": error.type, **error.arguments}, call_id])
            failed = failed or error
    context.renderer.json(responses)
    if failed is not None:
        raise failed


def _calls(context: CLIContext, source: str) -> list[Call]:
    """The calls in ``source``; one with no call id gets ``c1``, ``c2``..., by position."""
    text = context.stdin.read() if source == "-" else _read_text(source)
    try:
        value = json.loads(text)
    except ValueError as error:
        raise UsageError(f"the calls are not valid JSON: {error}") from error
    if not isinstance(value, list) or not value:
        raise UsageError("the calls must be a JSON array of [method, arguments, callId]")
    calls: list[Call] = []
    for number, item in enumerate(cast("list[object]", value), start=1):
        parts = cast("list[object]", item) if isinstance(item, list) else []
        if len(parts) not in (2, 3) or not isinstance(parts[0], str):
            raise UsageError(f"call {number} is not [method, arguments, callId]")
        if not isinstance(parts[1], dict):
            raise UsageError(f"call {number}: the arguments must be a JSON object")
        call_id = str(parts[2]) if len(parts) == 3 else f"c{number}"
        if call_id in {known for _, _, known in calls}:
            raise UsageError(f"the call id {call_id!r} is used twice")
        calls.append((parts[0], cast("dict[str, Any]", parts[1]), call_id))
    return calls


def _references(arguments: dict[str, Any], handles: dict[str, Handle[Any]]) -> dict[str, Any]:
    """``arguments`` with each ``#name`` back-reference aimed at the call it names."""
    resolved: dict[str, Any] = {}
    for key, value in arguments.items():
        if not key.startswith("#"):
            resolved[key] = value
            continue
        reference = cast("dict[str, Any]", value) if isinstance(value, dict) else {}
        source = handles.get(str(reference.get("resultOf")))
        path = reference.get("path")
        if source is None or not isinstance(path, str):
            raise UsageError(f"{key} needs the call id of an earlier call in resultOf, and a path")
        resolved[key[1:]] = source.ref(path)
    return resolved


def _read_text(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"cannot read {path}: {error.strerror or error}") from error
