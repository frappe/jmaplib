"""``jmap mail list`` and ``search``, and what every mail command shares.

A listing numbers its rows and remembers them (the cursor), so ``jmap read 3``
or ``jmap move 1-4 archive`` in a later run means rows of that listing. The
other mail commands live in :mod:`.mail_read` and :mod:`.mail_triage`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Final

import typer
from rich.markup import escape

from jmap.cli import format as fmt
from jmap.cli.commands import cli
from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError
from jmap.cli.ids import Listing, Mailbox, Ref, parse_refs
from jmap.cli.output import Column, OutputFormat, View, wire_of
from jmap.core.ids import parse_id
from jmap.mail.ops import queue_list
from jmap.mail.query import And, Node, Term, mentions, needs_snippets, parse, parse_sort, to_filter

app: typer.Typer = typer.Typer(no_args_is_help=True)

#: What makes a quoted argument a query of its own rather than one value.
_SYNTAX: Final = re.compile(r'[()"]|(?:^|\s)(?:OR|AND|NOT|-?[A-Za-z][\w-]*:)(?:\s|$|\S)')

#: The page size when none is asked for, and the page size of ``--all``.
PAGE: int = 25
ALL_PAGE: int = 256

Words = Annotated[list[str] | None, typer.Argument(help="Search terms: from:bob is:unread 7d.")]
FromOption = Annotated[str | None, typer.Option("--from", help="Sender contains this.")]
ToOption = Annotated[str | None, typer.Option("--to", help="A recipient contains this.")]
SubjectOption = Annotated[str | None, typer.Option(help="Subject contains this.")]
UnreadOption = Annotated[bool, typer.Option("--unread", help="Only unread mail.")]
FlaggedOption = Annotated[bool, typer.Option("--flagged", help="Only flagged mail.")]
AttachmentOption = Annotated[bool, typer.Option("--attachment", help="Only mail with files.")]
BeforeOption = Annotated[str | None, typer.Option(help="Received before: 2025-01-31, 7d.")]
AfterOption = Annotated[str | None, typer.Option(help="Received after: 2025-01-31, today.")]
SortOption = Annotated[str, typer.Option(help="Comma list: -date, subject, from, size, flagged.")]
LimitOption = Annotated[int, typer.Option("-n", "--limit", min=0, help="Messages per page.")]
PageOption = Annotated[int, typer.Option(min=1, help="Which page of --limit messages.")]
AllOption = Annotated[bool, typer.Option("--all", help="Every match, page after page.")]
CountOption = Annotated[bool, typer.Option("--count", help="Print the number of matches.")]
ThreadsOption = Annotated[bool, typer.Option("--threads", help="One row per conversation.")]


@dataclass(frozen=True, slots=True)
class Filters:
    """The filter options, which are shorthand for search terms."""

    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    unread: bool = False
    flagged: bool = False
    attachment: bool = False
    before: str | None = None
    after: str | None = None

    def terms(self) -> list[Term]:
        pairs = [
            ("from", self.sender),
            ("to", self.recipient),
            ("subject", self.subject),
            ("before", self.before),
            ("after", self.after),
            ("is", "unread" if self.unread else None),
            ("is", "flagged" if self.flagged else None),
            ("has", "attachment" if self.attachment else None),
        ]
        return [Term(key, value) for key, value in pairs if value]


@dataclass(frozen=True, slots=True)
class Selection:
    """The messages a command acts on."""

    ids: list[str]
    #: The mailbox they were listed from, when every one came from the listing.
    mailbox_id: str | None = None


@app.command("list")
def list_(
    ctx: typer.Context,
    words: Words = None,
    mailbox: Annotated[
        str | None, typer.Option("--in", help="Mailbox to list; the inbox by default.")
    ] = None,
    everywhere: Annotated[
        bool, typer.Option("-A", "--all-mailboxes", help="Every mailbox.")
    ] = False,
    sender: FromOption = None,
    recipient: ToOption = None,
    subject: SubjectOption = None,
    unread: UnreadOption = False,
    flagged: FlaggedOption = False,
    attachment: AttachmentOption = False,
    before: BeforeOption = None,
    after: AfterOption = None,
    sort: SortOption = "-date",
    limit: LimitOption = PAGE,
    page: PageOption = 1,
    every: AllOption = False,
    count: CountOption = False,
    threads: ThreadsOption = False,
) -> None:
    """List messages, newest first: the inbox, or the mailbox --in names."""
    filters = Filters(sender, recipient, subject, unread, flagged, attachment, before, after)
    run_listing(
        cli(ctx),
        words,
        filters,
        Scope(mailbox, everywhere),
        sort,
        limit,
        page,
        every,
        count,
        threads,
    )


@app.command()
def search(
    ctx: typer.Context,
    words: Words = None,
    sender: FromOption = None,
    recipient: ToOption = None,
    subject: SubjectOption = None,
    unread: UnreadOption = False,
    flagged: FlaggedOption = False,
    attachment: AttachmentOption = False,
    before: BeforeOption = None,
    after: AfterOption = None,
    sort: SortOption = "-date",
    limit: LimitOption = PAGE,
    page: PageOption = 1,
    every: AllOption = False,
    count: CountOption = False,
    threads: ThreadsOption = False,
) -> None:
    """Search every mailbox, and highlight what matched.

    Terms: from: to: cc: subject: body: in: notin: is:unread|flagged|answered|draft
    tag: has:attachment before: after: larger: smaller: header:Name=value. Combine
    with OR, NOT or -term, and (parentheses). A bare word searches everything.
    """
    filters = Filters(sender, recipient, subject, unread, flagged, attachment, before, after)
    scope = Scope(None, everywhere=True, highlight=True)
    run_listing(cli(ctx), words, filters, scope, sort, limit, page, every, count, threads)


@dataclass(frozen=True, slots=True)
class Scope:
    mailbox: str | None
    everywhere: bool = False
    highlight: bool = False


def run_listing(
    context: CLIContext,
    words: list[str] | None,
    filters: Filters,
    scope: Scope,
    sort: str,
    limit: int,
    page: int,
    every: bool,
    count: bool,
    threads: bool,
) -> None:
    text = query_text(words or [])
    node = combine(parse(text), filters.terms())
    listed = None
    if not scope.everywhere and not mentions(node, "in", "notin"):
        listed = context.mailbox(scope.mailbox or ".")
        node = combine(node, [Term("in", f"id:{listed['id']}")])

    def mailbox_id(name: str) -> str:
        return str(context.mailbox(name)["id"])

    query: dict[str, Any] = {
        "filter": to_filter(node, mailbox_id=mailbox_id) if node else None,
        "sort": parse_sort(sort),
        "collapse_threads": threads,
    }
    client = context.require("Email/query", "Email/get")
    if count:
        with client.batch() as batch:
            handles = queue_list(batch, limit=0, **query)
        context.out.file.write(f"{handles.query.result.total}\n")
        return
    highlight = (
        scope.highlight
        and needs_snippets(node)
        and client.capabilities.supports("SearchSnippet/get")
    )
    position = (page - 1) * limit
    rows, total, snippets = _fetch(context, query, position, limit, every, highlight)
    _show(context, rows, snippets, first=position + 1, total=total, listed=listed, sort=sort)
    context.cursor.record(
        Listing(
            ids=tuple(str(row["id"]) for row in rows),
            first=position + 1,
            mailbox_id=str(listed["id"]) if listed else None,
            query=text,
        )
    )


def _fetch(
    context: CLIContext,
    query: dict[str, Any],
    position: int,
    limit: int,
    every: bool,
    highlight: bool,
) -> tuple[list[dict[str, Any]], int | None, dict[str, dict[str, Any]]]:
    """Rows in query order, the total, and snippets by email id."""
    client = context.client()
    rows: list[dict[str, Any]] = []
    snippets: dict[str, dict[str, Any]] = {}
    while True:
        with client.batch() as batch:
            handles = queue_list(
                batch,
                position=position + len(rows),
                limit=ALL_PAGE if every else limit,
                calculate_total=not every,
                snippets=highlight,
                **query,
            )
        ids = handles.query.result.ids
        found = {item["id"]: item for item in wire_of(handles.emails.result)["list"]}
        rows += [found[email_id] for email_id in ids if email_id in found]
        if handles.snippets is not None:
            for snippet in wire_of(handles.snippets.result)["list"]:
                snippets[snippet["emailId"]] = snippet
        if not every:
            return rows, handles.query.result.total, snippets
        if not ids:
            return rows, len(rows), snippets


def _show(
    context: CLIContext,
    rows: list[dict[str, Any]],
    snippets: dict[str, dict[str, Any]],
    *,
    first: int,
    total: int | None,
    listed: Mailbox | None,
    sort: str,
) -> None:
    table = context.renderer.format is OutputFormat.TABLE
    outgoing = listed is not None and listed.get("role") in {"sent", "drafts"}
    columns = [
        Column("FLAGS", "keywords", fmt.flags),
        _date_column(table),
        _address_column("to" if outgoing else "from"),
        _snippet_column("SUBJECT", "subject", snippets),
    ]
    if snippets:
        columns.append(_snippet_column("MATCH", "preview", snippets))
    columns.append(
        Column("SIZE", "size", (lambda r: fmt.size(r.get("size"))) if table else None, "right")
    )
    footer = _footer(rows, first, total, listed, sort)
    context.renderer.render(View(rows, columns, first_number=first, footer=footer))


def _date_column(table: bool) -> Column:
    """People's dates in a table; the server's ISO dates in plain text."""
    if not table:
        return Column("DATE", "receivedAt")
    now = datetime.now().astimezone()
    return Column("DATE", "receivedAt", lambda row: fmt.when(row.get("receivedAt"), now=now))


def _address_column(key: str) -> Column:
    return Column(key.upper(), key, lambda row: fmt.address(row.get(key)))


def _snippet_column(header: str, key: str, snippets: dict[str, dict[str, Any]]) -> Column:
    """``key`` with search matches highlighted where the server sent a snippet."""

    def snippet(row: Mapping[str, Any]) -> str | None:
        found: str | None = (snippets.get(row["id"]) or {}).get(key)
        return found

    def plain(row: Mapping[str, Any]) -> str:
        found = snippet(row)
        return fmt.snippet_plain(found) if found else str(row.get(key) or "")

    def markup(row: Mapping[str, Any]) -> str:
        found = snippet(row)
        return fmt.snippet_markup(found) if found else escape(str(row.get(key) or ""))

    return Column(header, key, plain, markup=markup)


def _footer(
    rows: list[dict[str, Any]], first: int, total: int | None, listed: Mailbox | None, sort: str
) -> str:
    shown = f"{first}-{first + len(rows) - 1}" if rows else "no messages"
    parts = [shown + (f" of {total}" if rows and total is not None else "")]
    if listed:
        parts.append(str(listed.get("name")))
    parts.append(sort)
    return " · ".join(parts)


def query_text(words: list[str]) -> str:
    """The words of a query as one string, keeping a shell-quoted value together.

    ``jmap ls subject:"two words"`` reaches us as ``subject:two words``; the
    quotes go back around the value so the parser sees one term. A quoted
    argument holding a whole query (``'from:bob OR is:flagged'``) is left as is.
    """
    quoted: list[str] = []
    for word in words:
        key, colon, value = word.partition(":")
        if colon and key.isidentifier() and " " in value and not _SYNTAX.search(value):
            quoted.append(f'{key}:"{value}"')
        else:
            quoted.append(word)
    return " ".join(quoted)


def combine(node: Node | None, terms: list[Term]) -> Node | None:
    """``node`` ANDed with ``terms``."""
    items: list[Node] = [*(node.items if isinstance(node, And) else [node] if node else []), *terms]
    if not items:
        return None
    return items[0] if len(items) == 1 else And(tuple(items))


def select(context: CLIContext, words: list[str]) -> Selection:
    """The ids ``words`` refer to: listing numbers, ids, Message-IDs or threads."""
    refs = parse_refs(words)
    lookups = [ref for ref in refs if ref.kind in {"msgid", "thread"}]
    found = _look_up(context, lookups) if lookups else {}
    ids: list[str] = []
    for ref in refs:
        if ref.kind == "index":
            ids.append(context.cursor.resolve(int(ref.value)))
        elif ref.kind == "id":
            ids.append(parse_id(ref.value))
        else:
            ids += found[ref]
    unique = list(dict.fromkeys(ids))
    from_listing = all(ref.kind == "index" for ref in refs)
    listing = context.cursor.listing if from_listing else None
    return Selection(unique, listing.mailbox_id if listing else None)


def _look_up(context: CLIContext, refs: list[Ref]) -> dict[Ref, list[str]]:
    """Email ids for Message-IDs and threads, in one request."""
    client = context.require("Email/query", "Thread/get")
    with client.batch() as batch:
        handles = {
            ref: batch.add("Email/query", {"filter": {"header": ["Message-ID", ref.value]}})
            if ref.kind == "msgid"
            else batch.add("Thread/get", {"ids": [ref.value]})
            for ref in refs
        }
    found: dict[Ref, list[str]] = {}
    for ref, handle in handles.items():
        result = wire_of(handle.result)
        ids = (
            result.get("ids")
            if ref.kind == "msgid"
            else [
                email_id
                for thread in result.get("list", [])
                for email_id in thread.get("emailIds", [])
            ]
        )
        if not ids:
            raise NotFoundError(f"no message has {ref.kind} {ref.value!r}")
        found[ref] = [str(email_id) for email_id in ids]
    return found
