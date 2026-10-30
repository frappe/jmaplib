"""``jmap identity``: the addresses you may send from."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.mail.addresses import format_addresses, parse_addresses
from jmap.models.mail.objects import EmailAddress

app: typer.Typer = typer.Typer(no_args_is_help=True)

Which = Annotated[str, typer.Argument(help="The identity, by id or address.")]
Name = Annotated[str | None, typer.Option(help="The display name.")]
ReplyTo = Annotated[str | None, typer.Option("--reply-to", help="Replies go here.")]
Bcc = Annotated[str | None, typer.Option(help="Always blind-copy these.")]
Signature = Annotated[str | None, typer.Option(help="Plain-text signature.")]
HtmlSignature = Annotated[str | None, typer.Option("--html-signature", help="HTML signature.")]

_COLUMNS = [
    Column("ID", "id"),
    Column("NAME", "name"),
    Column("EMAIL", "email"),
    Column("REPLY-TO", "replyTo", lambda i: _addresses(i.get("replyTo"))),
    Column("DELETABLE", "mayDelete", lambda i: "yes" if i.get("mayDelete") else "no"),
]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List your identities."""
    context = cli(ctx)
    context.renderer.render(View(_all(context), _COLUMNS))


@app.command()
def show(ctx: typer.Context, identity: Which) -> None:
    """Show one identity, with its signatures."""
    context = cli(ctx)
    columns = [
        *_COLUMNS,
        Column("Bcc", "bcc", lambda i: _addresses(i.get("bcc"))),
        Column("Signature", "textSignature"),
        Column("HTML signature", "htmlSignature"),
    ]
    context.renderer.render(View([_find(context, identity)], columns, single=True))


@app.command()
def create(
    ctx: typer.Context,
    email: Annotated[str, typer.Option(help="The address; the server decides which it allows.")],
    name: Name = None,
    reply_to: ReplyTo = None,
    bcc: Bcc = None,
    signature: Signature = None,
    html_signature: HtmlSignature = None,
) -> None:
    """Add an identity."""
    context = cli(ctx)
    created = {"email": email, **_fields(name, reply_to, bcc, signature, html_signature)}
    _set(context, {"create": {"new": created}}, f"created the identity {email}")


@app.command()
def update(
    ctx: typer.Context,
    identity: Which,
    name: Name = None,
    reply_to: ReplyTo = None,
    bcc: Bcc = None,
    signature: Signature = None,
    html_signature: HtmlSignature = None,
) -> None:
    """Change an identity's name, reply-to, bcc or signatures."""
    context = cli(ctx)
    found = _find(context, identity)
    patch = _fields(name, reply_to, bcc, signature, html_signature)
    if not patch:
        raise UsageError("nothing to change", hint="give --name, --signature, ...")
    _set(context, {"update": {found["id"]: patch}}, f"updated {found.get('email')}")


@app.command()
def delete(ctx: typer.Context, identity: Which) -> None:
    """Delete an identity, after you confirm."""
    context = cli(ctx)
    found = _find(context, identity)
    context.confirm(f"Delete the identity {found.get('email')}?")
    _set(context, {"destroy": [found["id"]]}, f"deleted {found.get('email')}")


def _all(context: CLIContext) -> list[dict[str, Any]]:
    client = context.require("Identity/get")
    with client.batch() as batch:
        handle = batch.add("Identity/get", {"ids": None})
    items: list[dict[str, Any]] = wire_of(handle.result)["list"]
    return items


def _find(context: CLIContext, wanted: str) -> dict[str, Any]:
    for item in _all(context):
        if wanted == item.get("id") or wanted.lower() == str(item.get("email", "")).lower():
            return item
    raise NotFoundError(f"no identity {wanted!r}", hint="see 'jmap identity list'")


def _fields(
    name: str | None,
    reply_to: str | None,
    bcc: str | None,
    signature: str | None,
    html_signature: str | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    try:
        if reply_to is not None:
            fields["replyTo"] = [a.to_wire() for a in parse_addresses(reply_to)] or None
        if bcc is not None:
            fields["bcc"] = [a.to_wire() for a in parse_addresses(bcc)] or None
    except ValueError as error:
        raise UsageError(str(error)) from error
    for key, value in (
        ("name", name),
        ("textSignature", signature),
        ("htmlSignature", html_signature),
    ):
        if value is not None:
            fields[key] = value
    return fields


def _set(context: CLIContext, arguments: dict[str, Any], done: str) -> None:
    client = context.require("Identity/set")
    with client.batch() as batch:
        handle = batch.add("Identity/set", arguments)
    report_set(context, handle.result, done)


def _addresses(value: Any) -> str:
    items: list[dict[str, Any]] = value or []
    return format_addresses(EmailAddress.from_wire(item) for item in items)
