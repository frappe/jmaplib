"""``jmap push``: push subscriptions, where the server POSTs changes to a URL.

A subscription is tied to the credentials that made it and is shown only to
them. The server POSTs a verification code to the URL at once and sends nothing
more until ``push verify`` returns the code. ``--encrypt`` makes a key pair
(this needs ``jmaplib[push]``), gives the server the public half, and keeps the
private half in the keyring under the subscription's id, for ``push decode``.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from jmap.cli.commands import cli, comma_list, report_set
from jmap.cli.commands.blob import read_source
from jmap.cli.errors import CLIError, NotFoundError, UnsupportedError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.push import (
    InsecurePushUrlError,
    PushKeyPair,
    PushPayloadError,
    application_server_key,
    new_subscription,
    read_push,
    renewal_update,
    verification_update,
)

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext

app: typer.Typer = typer.Typer(no_args_is_help=True)

Subscription = Annotated[str, typer.Argument(help="The subscription's id.")]

#: ``+7d``, ``12h`` or ``30m`` from now.
_SPAN: Final = re.compile(r"\+?(\d+)([mhd])")
_UNITS: Final = {"m": "minutes", "h": "hours", "d": "days"}
_COLUMNS = [
    Column("ID", "id"),
    Column("DEVICE", "deviceClientId"),
    Column("TYPES", "types", lambda i: ", ".join(i.get("types") or []) or "all"),
    Column("EXPIRES", "expires"),
    Column("VERIFIED", "verificationCode", lambda i: "yes" if i.get("verificationCode") else "no"),
]


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List the subscriptions these credentials made."""
    context = cli(ctx)
    client = context.require("PushSubscription/get")
    with client.batch() as batch:
        handle = batch.add("PushSubscription/get", {"ids": None})
    context.renderer.render(View(wire_of(handle.result)["list"], _COLUMNS))


@app.command()
def add(
    ctx: typer.Context,
    url: Annotated[str, typer.Option(help="The https:// URL the server POSTs to.")],
    types: Annotated[
        list[str] | None,
        typer.Option(help="Types to push, such as Email; every type if not given."),
    ] = None,
    expires: Annotated[
        str | None, typer.Option(help="When it ends: +7d, 12h, or 2026-12-01T00:00:00Z.")
    ] = None,
    encrypt: Annotated[
        bool, typer.Option("--encrypt", help="Encrypt what is pushed; keep the key here.")
    ] = False,
) -> None:
    """Subscribe a URL; the server then POSTs a code to it for 'push verify'."""
    context = cli(ctx)
    pair = _generate() if encrypt else None
    try:
        creation = new_subscription(
            f"jmap-cli/{context.account.name}",
            url,
            types=comma_list(types) or None,
            keys=pair.keys if pair is not None else None,
            expires=_expiry(expires) if expires is not None else None,
        )
    except InsecurePushUrlError as error:
        raise UsageError(str(error)) from error
    client = context.require("PushSubscription/set")
    with client.batch() as batch:
        handle = batch.add("PushSubscription/set", {"create": {"push": creation}})
    response: dict[str, Any] = wire_of(handle.result)
    every: dict[str, dict[str, Any]] = response.get("created") or {}
    created = every.get("push") or {}
    if pair is not None and created:
        keys = {"privateKey": pair.private_key, "auth": pair.auth}
        context.secrets.set(_owner(context, created["id"]), "push", json.dumps(keys))
    report_set(context, handle.result, "created {n} push subscription(s)")
    context.renderer.note(f"finish with 'jmap push verify {created['id']} CODE'")


@app.command()
def verify(
    ctx: typer.Context,
    subscription: Subscription,
    code: Annotated[str, typer.Argument(help="The code the server POSTed to the URL.")],
) -> None:
    """Send back the verification code, so the server starts pushing."""
    _update(cli(ctx), subscription, verification_update(code), f"verified {subscription}")


@app.command()
def renew(
    ctx: typer.Context,
    subscription: Subscription,
    expires: Annotated[str, typer.Option(help="The new end: +7d, 12h, or a UTC date.")] = "+7d",
) -> None:
    """Move a subscription's end; the server may set an earlier one."""
    _update(cli(ctx), subscription, renewal_update(_expiry(expires)), f"renewed {subscription}")


@app.command()
def remove(ctx: typer.Context, subscription: Subscription) -> None:
    """Delete a subscription, and any key kept for it."""
    context = cli(ctx)
    client = context.require("PushSubscription/set")
    with client.batch() as batch:
        handle = batch.add("PushSubscription/set", {"destroy": [subscription]})
    if subscription in (wire_of(handle.result).get("destroyed") or []):
        context.secrets.delete(_owner(context, subscription), "push")
    report_set(context, handle.result, f"removed {subscription}")


@app.command()
def keys(ctx: typer.Context) -> None:
    """Print the server's VAPID key (RFC 9749), for a browser push service."""
    context = cli(ctx)
    key = application_server_key(context.client().session)
    if key is None:
        raise UnsupportedError("this server does not advertise urn:ietf:params:jmap:webpush-vapid")
    if context.options.json_mode:
        context.renderer.json({"applicationServerKey": key})
        return
    context.out.file.write(f"{key}\n")


@app.command()
def decode(
    ctx: typer.Context,
    source: Annotated[
        str, typer.Argument(help="A POST body saved to a file, or - for stdin.")
    ] = "-",
    subscription: Annotated[
        str | None, typer.Option(help="Decrypt with the key kept for this subscription.")
    ] = None,
) -> None:
    """Print what one push POST says, as JSON: a change, a verification, or an alert."""
    context = cli(ctx)
    body = read_source(context, source)
    pair = _stored(context, subscription) if subscription is not None else None
    try:
        pushed = read_push(body, pair)
    except PushPayloadError as error:
        raise CLIError(str(error)) from error
    context.renderer.json(pushed)


def _update(context: CLIContext, subscription: str, patch: dict[str, Any], done: str) -> None:
    client = context.require("PushSubscription/set")
    with client.batch() as batch:
        handle = batch.add("PushSubscription/set", {"update": {subscription: patch}})
    report_set(context, handle.result, done)


def _expiry(text: str) -> str:
    """``+7d`` as a UTC date that far ahead; anything else as it is, to be checked."""
    span = _SPAN.fullmatch(text.strip())
    if span is None:
        return text
    moment = datetime.now(UTC) + timedelta(**{_UNITS[span[2]]: int(span[1])})
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate() -> PushKeyPair:
    _need_crypto()
    return PushKeyPair.generate()


def _stored(context: CLIContext, subscription: str) -> PushKeyPair:
    saved = context.secrets.get(_owner(context, subscription), "push")
    if saved is None:
        raise NotFoundError(f"no key is kept for the push subscription {subscription}")
    _need_crypto()
    keys = json.loads(saved)
    return PushKeyPair(keys["privateKey"], keys["auth"])


def _need_crypto() -> None:
    if importlib.util.find_spec("cryptography") is None:
        raise UsageError(
            "encrypted push needs the cryptography package", hint="pip install 'jmaplib[push]'"
        )


def _owner(context: CLIContext, subscription: str) -> str:
    """Where a subscription's key is kept: per named account, per subscription."""
    return f"{context.account.name}/push/{subscription}"
