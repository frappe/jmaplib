"""``jmap account``: named accounts, each one server and one credential.

The JMAP accounts *inside* a session (personal, shared, delegated) are never
configured here, only observed: ``account show`` lists what the server returns,
and ``--account-id`` picks one of them.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.auth import make_auth
from jmap.cli.commands import cli
from jmap.cli.config import AccountConfig, AuthKind, ResolvedAccount, from_config
from jmap.cli.context import CLIContext
from jmap.cli.errors import AuthError, NotFoundError, UsageError
from jmap.cli.output import Column, View
from jmap.cli.secrets import KINDS, KeyringUnavailableError
from jmap.client import JMAPClient

app: typer.Typer = typer.Typer(no_args_is_help=True)

Name = Annotated[str, typer.Argument(help="The named account.")]
OptionalName = Annotated[
    str | None, typer.Argument(help="The named account; the current one if omitted.")
]
PasswordStdin = Annotated[
    bool, typer.Option("--password-stdin", help="Read the password from stdin.")
]
TokenStdin = Annotated[bool, typer.Option("--token-stdin", help="Read the token from stdin.")]
NoVerify = Annotated[bool, typer.Option("--no-verify", help="Do not connect to check it.")]


@app.command()
def add(
    ctx: typer.Context,
    name: Name,
    url: Annotated[str, typer.Option(help="Session URL, or anything redirecting to it.")] = "",
    address: Annotated[
        str, typer.Option(help="Email address; finds the server when --url is not given.")
    ] = "",
    auth: Annotated[str | None, typer.Option(help="password (the default) or token.")] = None,
    username: Annotated[str, typer.Option(help="Login name; defaults to the address.")] = "",
    identity: Annotated[str, typer.Option(help="Default From address.")] = "",
    ca_bundle: Annotated[str, typer.Option(help="CA certificates to trust.")] = "",
    trust_srv: Annotated[
        list[str] | None, typer.Option(help="SRV target host to trust (repeatable).")
    ] = None,
    default: Annotated[bool, typer.Option("--default", help="Make it the default.")] = False,
    password_stdin: PasswordStdin = False,
    token_stdin: TokenStdin = False,
    no_verify: NoVerify = False,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing account.")] = False,
) -> None:
    """Add a named account, check it connects, and store its secret in the keyring.

    The global --account-id, --experimental and --insecure are stored with it.
    """
    context = cli(ctx)
    config = context.config
    if name in config.accounts and not force:
        raise UsageError(f"account {name!r} exists", hint="pass --force to replace it")
    if not url and not address:
        raise UsageError("give --url, or --address to find the server from it")
    stored = AccountConfig(
        address=address,
        url=url,
        auth=_auth_kind(auth, password_stdin=password_stdin, token_stdin=token_stdin),
        username=username,
        account_id=context.flags.account_id or "",
        identity=identity,
        experimental=context.flags.experimental,
        trusted_srv_targets=trust_srv or [],
        ca_bundle=ca_bundle,
        insecure=context.flags.insecure,
    )
    resolved = from_config(name, stored, config.defaults)
    secret = _new_secret(context, resolved, password_stdin or token_stdin, ask_environment=True)
    if not no_verify:
        _connected(context, context.connect(resolved, make_auth(resolved, secret)))
    accounts = {**config.accounts, name: stored}
    first = default or not config.default_account
    context.save_config(
        config.model_copy(
            update={
                "accounts": accounts,
                "default_account": name if first else config.default_account,
            }
        )
    )
    _store(context, resolved, secret)
    context.renderer.note(f"added {name!r} to {context.config_path}")


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List the named accounts in the configuration file."""
    context = cli(ctx)
    config = context.config
    items = [
        {"name": name, **account.model_dump(mode="json")}
        for name, account in sorted(config.accounts.items())
    ]
    context.renderer.render(
        View(
            items=items,
            columns=[
                Column("", "default", lambda i: "*" if i["name"] == config.default_account else ""),
                Column("NAME", "name"),
                Column("ADDRESS", "address", lambda i: i["address"] or i["username"]),
                Column("SERVER", "url", lambda i: i["url"] or "(discovered)"),
                Column("AUTH", "auth"),
            ],
        )
    )


@app.command()
def show(ctx: typer.Context, name: OptionalName = None) -> None:
    """List the JMAP accounts the server's session returns for a named account."""
    context = cli(ctx)
    context.use_account(name)
    client = context.client()
    session = client.session
    accounts = session.raw.get("accounts", {})
    items = [{"id": key, **accounts.get(key, {})} for key in session.accounts]
    context.renderer.render(
        View(
            items=items,
            columns=[
                Column("", "selected", lambda i: "*" if i["id"] == client.default_account else ""),
                Column("ID", "id"),
                Column("NAME", "name"),
                Column("PERSONAL", "isPersonal", lambda i: _yes(i.get("isPersonal"))),
                Column("READ-ONLY", "isReadOnly", lambda i: _yes(i.get("isReadOnly"))),
                Column("PRIMARY FOR", "primaryFor", lambda i: _primary_for(client, i["id"])),
            ],
            footer=f"{session.username} at {client.session_url}",
        )
    )


@app.command()
def remove(ctx: typer.Context, name: Name) -> None:
    """Remove a named account and its stored secrets."""
    context = cli(ctx)
    config = context.config
    if name not in config.accounts:
        raise NotFoundError(f"no account named {name!r}", hint="see 'jmap account list'")
    accounts = {key: value for key, value in config.accounts.items() if key != name}
    default = "" if config.default_account == name else config.default_account
    context.save_config(
        config.model_copy(update={"accounts": accounts, "default_account": default})
    )
    try:
        for kind in KINDS:
            context.secrets.delete(name, kind)
    except KeyringUnavailableError as error:
        context.renderer.note(f"secrets not removed: {error}")
    context.renderer.note(f"removed {name!r}")


@app.command("set-default")
def set_default(ctx: typer.Context, name: Name) -> None:
    """Make a named account the one used when -a is not given."""
    context = cli(ctx)
    config = context.config
    if name not in config.accounts:
        raise NotFoundError(f"no account named {name!r}", hint="see 'jmap account list'")
    context.save_config(config.model_copy(update={"default_account": name}))


@app.command("test")
def check(ctx: typer.Context, name: OptionalName = None) -> None:
    """Connect, call Core/echo, and report what the session says."""
    context = cli(ctx)
    context.use_account(name)
    client = context.client()
    client.echo(ping="jmap")
    _connected(context, client)


@app.command()
def login(
    ctx: typer.Context,
    name: OptionalName = None,
    password_stdin: PasswordStdin = False,
    token_stdin: TokenStdin = False,
    no_verify: NoVerify = False,
) -> None:
    """Enter a named account's password or token again, and store it in the keyring."""
    context = cli(ctx)
    context.use_account(name)
    resolved = context.account
    if resolved.synthetic:
        raise UsageError(f"{resolved.name!r} comes from JMAP_URL; add it with 'jmap account add'")
    _auth_kind(resolved.auth, password_stdin=password_stdin, token_stdin=token_stdin)
    secret = _new_secret(context, resolved, password_stdin or token_stdin, ask_environment=False)
    if not no_verify:
        context.connect(resolved, make_auth(resolved, secret))
    _store(context, resolved, secret)
    context.renderer.note(f"stored the {resolved.auth} for {resolved.name!r}")


def _auth_kind(given: str | None, *, password_stdin: bool, token_stdin: bool) -> AuthKind:
    if password_stdin and token_stdin:
        raise UsageError("give --password-stdin or --token-stdin, not both")
    wanted = given or ("token" if token_stdin else "password")
    if wanted not in KINDS:
        raise UsageError(f"--auth must be one of {', '.join(KINDS)}, not {wanted!r}")
    if (wanted == "token" and password_stdin) or (wanted == "password" and token_stdin):
        raise UsageError(f"this account uses a {wanted}; read it with --{wanted}-stdin")
    return "token" if wanted == "token" else "password"


def _new_secret(
    context: CLIContext, account: ResolvedAccount, from_stdin: bool, *, ask_environment: bool
) -> str:
    """The secret to store: from stdin, the environment (``add`` only), or a prompt."""
    if from_stdin:
        secret = context.stdin.readline().rstrip("\r\n")
        if not secret:
            raise UsageError(f"no {account.auth} on stdin")
        return secret
    if ask_environment:
        return context.credentials().secret(account)
    if context.prompter.interactive:
        return context.prompter.secret(f"{account.auth.capitalize()} for {account.name}")
    raise AuthError(
        f"no terminal to ask for the {account.auth}", hint=f"pipe it to --{account.auth}-stdin"
    )


def _store(context: CLIContext, account: ResolvedAccount, secret: str) -> None:
    if context.flags.no_keyring:
        context.renderer.note(f"the {account.auth} was not stored (--no-keyring)")
        return
    try:
        context.secrets.set(account.name, account.auth, secret)
    except KeyringUnavailableError as error:
        context.renderer.note(f"the {account.auth} was not stored: {error}")


def _connected(context: CLIContext, client: JMAPClient) -> None:
    session = client.session
    item: dict[str, Any] = {
        "username": session.username,
        "sessionUrl": client.session_url,
        "apiUrl": session.api_url,
        "accounts": len(session.accounts),
        "capabilities": len(session.capabilities),
    }
    context.renderer.render(
        View(
            items=[item],
            columns=[
                Column("Username", "username"),
                Column("Session", "sessionUrl"),
                Column("API", "apiUrl"),
                Column("JMAP accounts", "accounts"),
                Column("Capabilities", "capabilities"),
            ],
            single=True,
        )
    )


def _primary_for(client: JMAPClient, account_id: str) -> str:
    urns = sorted(
        urn for urn, primary in client.session.primary_accounts.items() if primary == account_id
    )
    return ", ".join(_short_urn(urn) for urn in urns)


def _short_urn(urn: str) -> str:
    return urn.removeprefix("urn:ietf:params:jmap:")


def _yes(value: object) -> str:
    return "yes" if value else "no"
