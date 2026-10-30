"""``jmap submission``: messages sent or waiting to be, and undoing a held one."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from jmap.cli.commands import cli, report_set
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError
from jmap.cli.output import Column, View, wire_of
from jmap.core.narrow import is_object

app: typer.Typer = typer.Typer(no_args_is_help=True)

Which = Annotated[str, typer.Argument(help="The submission id.")]


@app.command("list")
def list_(
    ctx: typer.Context,
    pending: Annotated[
        bool, typer.Option("--pending", help="Only those that can still be cancelled.")
    ] = False,
) -> None:
    """List submissions, with each message's subject."""
    context = cli(ctx)
    client = context.require("EmailSubmission/query", "EmailSubmission/get", "Email/get")
    query: dict[str, Any] = {"filter": {"undoStatus": "pending"}} if pending else {}
    with client.batch() as batch:
        found = batch.add("EmailSubmission/query", query)
        submissions = batch.add("EmailSubmission/get", {"ids": found.ref_ids()})
        emails = batch.add(
            "Email/get",
            {"ids": submissions.ref_list("emailId"), "properties": ["subject", "to"]},
        )
    _render(context, wire_of(submissions.result)["list"], wire_of(emails.result)["list"])


@app.command()
def show(ctx: typer.Context, submission: Which) -> None:
    """Show one submission: its status, envelope and delivery."""
    context = cli(ctx)
    item = _get(context, submission)
    columns = [
        Column("ID", "id"),
        Column("Status", "undoStatus"),
        Column("Send at", "sendAt"),
        Column("Email", "emailId"),
        Column("Identity", "identityId"),
        Column("Delivery", "deliveryStatus", lambda i: _delivery(i.get("deliveryStatus"))),
    ]
    context.renderer.render(View([item], columns, single=True))


@app.command()
def cancel(ctx: typer.Context, submission: Which) -> None:
    """Cancel a submission that has not gone yet, such as one held with --send-at."""
    context = cli(ctx)
    client = context.require("EmailSubmission/set")
    with client.batch() as batch:
        handle = batch.add(
            "EmailSubmission/set", {"update": {submission: {"undoStatus": "canceled"}}}
        )
    report_set(context, handle.result, f"cancelled {submission}")


def _get(context: CLIContext, wanted: str) -> dict[str, Any]:
    client = context.require("EmailSubmission/get")
    with client.batch() as batch:
        handle = batch.add("EmailSubmission/get", {"ids": [wanted]})
    found: list[dict[str, Any]] = wire_of(handle.result)["list"]
    if not found:
        raise NotFoundError(f"no submission {wanted!r}", hint="see 'jmap submission list'")
    return found[0]


def _render(
    context: CLIContext, submissions: list[dict[str, Any]], emails: list[dict[str, Any]]
) -> None:
    subjects = {item["id"]: item.get("subject") or "" for item in emails}
    columns = [
        Column("ID", "id"),
        Column("STATUS", "undoStatus"),
        Column("SEND AT", "sendAt"),
        Column("SUBJECT", "emailId", lambda i: subjects.get(i.get("emailId"), "")),
    ]
    context.renderer.render(View(submissions, columns))


def _delivery(value: Any) -> str:
    if not is_object(value):
        return ""
    statuses: dict[str, Any] = value
    return ", ".join(
        f"{address}: {status.get('delivered', '?')}" for address, status in statuses.items()
    )
