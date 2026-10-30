"""``jmap principal``: the people, groups and resources a server knows (RFC 9670)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import typer

from jmap import sharing
from jmap.capabilities.principals import PRINCIPALS_URN
from jmap.cli.commands import cli
from jmap.cli.errors import NotFoundError, UsageError
from jmap.cli.output import Column, View, wire_of
from jmap.core.ids import Id
from jmap.models.principals import PRINCIPAL_TYPES

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext
    from jmap.client import BatchContext, JMAPClient
    from jmap.core.invocation import Handle

app: typer.Typer = typer.Typer(no_args_is_help=True)

Which = Annotated[str, typer.Argument(help="The principal, by id, address or name.")]

_COLUMNS = [
    Column("ID", "id"),
    Column("TYPE", "type"),
    Column("NAME", "name"),
    Column("EMAIL", "email"),
]


@app.command("list")
def list_(
    ctx: typer.Context,
    text: Annotated[str | None, typer.Argument(help="Name, address or description.")] = None,
    kind: Annotated[
        str | None, typer.Option("--type", help="individual, group, resource, location, other.")
    ] = None,
) -> None:
    """List principals, or those that match TEXT."""
    context = cli(ctx)
    if kind is not None and kind not in PRINCIPAL_TYPES:
        raise UsageError(f"no principal type {kind!r}", hint=", ".join(sorted(PRINCIPAL_TYPES)))
    condition = {key: value for key, value in (("text", text), ("type", kind)) if value}
    client = context.require("Principal/query", "Principal/get")
    account = principal_account(client)
    with client.batch() as batch:
        query = {"filter": condition} if condition else {}
        found = batch.add("Principal/query", query, account_id=account)
        principals = batch.add("Principal/get", {"ids": found.ref_ids()}, account_id=account)
    context.renderer.render(View(wire_of(principals.result)["list"], _COLUMNS))


@app.command()
def show(ctx: typer.Context, principal: Which) -> None:
    """Show one principal, with its capabilities and accounts."""
    context = cli(ctx)
    client = context.require("Principal/get", "Principal/query")
    _show(context, find_principal(client, principal, principal_account(client)))


@app.command()
def me(ctx: typer.Context) -> None:
    """Show the principal the server says is you."""
    context = cli(ctx)
    client = context.require("Principal/get")
    account = principal_account(client)
    mine = sharing.me(client.session, account) if account else None
    if mine is None:
        raise NotFoundError("the server names no principal for you")
    with client.batch() as batch:
        handle = batch.add("Principal/get", {"ids": [mine]}, account_id=account)
    found: list[dict[str, Any]] = wire_of(handle.result)["list"]
    if not found:
        raise NotFoundError(f"your principal {mine!r} is not there")
    _show(context, found[0])


def principal_account(client: JMAPClient, data_account: str | None = None) -> Id | None:
    """The account for ``Principal/*``: the one ``data_account`` names, else the primary one."""
    named = sharing.principal_account(client.session, data_account) if data_account else None
    if named:
        return Id(named)
    return client.session.capability_account(PRINCIPALS_URN, client.default_account)


def find_principal(client: JMAPClient, reference: str, account: Id | None) -> dict[str, Any]:
    """The principal ``reference`` names, in one request."""
    with client.batch() as batch:
        lookup = Lookup.queue(client, batch, reference, account)
    return lookup.result()


def labels(client: JMAPClient, ids: list[str], account: Id | None) -> dict[str, str]:
    """``Name <address>`` for each principal id, or nothing if the server has no principals."""
    if not ids or not client.capabilities.supports("Principal/get"):
        return {}
    with client.batch() as batch:
        handle = batch.add(
            "Principal/get", {"ids": ids, "properties": ["name", "email"]}, account_id=account
        )
    return {str(item["id"]): label(item) for item in wire_of(handle.result)["list"]}


def label(principal: dict[str, Any]) -> str:
    name, email = principal.get("name"), principal.get("email")
    if name and email and str(name).lower() != str(email).lower():
        return f"{name} <{email}>"
    return str(name or email or principal["id"])


@dataclass(frozen=True, slots=True)
class Lookup:
    """A principal looked up by id, and by text in case it is not an id."""

    reference: str
    by_id: Handle[Any] | None
    by_text: Handle[Any] | None

    @classmethod
    def queue(
        cls, client: JMAPClient, batch: BatchContext, reference: str, account: Id | None
    ) -> Lookup:
        """Queue the calls; a server with no principals gets none, and ids pass through."""
        if not client.capabilities.supports("Principal/query"):
            return cls(reference, None, None)
        by_id = batch.add("Principal/get", {"ids": [reference]}, account_id=account)
        # An address goes to the email filter: Stalwart's text filter does not match one whole.
        condition = {"email": reference} if "@" in reference else {"text": reference}
        found = batch.add("Principal/query", {"filter": condition}, account_id=account)
        by_text = batch.add("Principal/get", {"ids": found.ref_ids()}, account_id=account)
        return cls(reference, by_id, by_text)

    def result(self) -> dict[str, Any]:
        if self.by_id is None or self.by_text is None:
            return {"id": self.reference}
        exact: list[dict[str, Any]] = wire_of(self.by_id.result)["list"]
        if exact:
            return exact[0]
        return self._match(wire_of(self.by_text.result)["list"])

    def _match(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        wanted = self.reference.lower()
        named = [
            item
            for item in candidates
            if wanted in {str(item.get("email") or "").lower(), str(item.get("name") or "").lower()}
        ]
        chosen = named or candidates
        if len(chosen) == 1:
            return chosen[0]
        if not chosen:
            raise NotFoundError(
                f"no principal {self.reference!r}", hint="see 'jmap principal list'"
            )
        names = ", ".join(label(item) for item in chosen)
        raise UsageError(f"{self.reference!r} could be {names}", hint="give the id")


def _show(context: CLIContext, principal: dict[str, Any]) -> None:
    columns = [
        *_COLUMNS,
        Column("Description", "description"),
        Column("Time zone", "timeZone"),
        Column("Capabilities", "capabilities", lambda i: ", ".join(i.get("capabilities") or {})),
        Column("Accounts", "accounts", lambda i: ", ".join(i.get("accounts") or {})),
    ]
    context.renderer.render(View([principal], columns, single=True))
