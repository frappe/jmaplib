"""``jmap mail list`` and ``search``, and what every mail command shares.

A listing numbers its rows and remembers them (the cursor), so ``jmap read 3``
or ``jmap move 1-4 archive`` in a later run means rows of that listing. The
other mail commands live in :mod:`.mail_read` and :mod:`.mail_triage`, and
:mod:`.mail_select` turns their arguments into message ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Final

import typer

from jmap.cli.commands import cli
from jmap.cli.commands.mail_columns import COLUMNS, ListingColumns, footer
from jmap.cli.commands.mail_select import select
from jmap.cli.completers import MAIL_SORT, comma_list, mailboxes
from jmap.cli.context import CLIContext
from jmap.cli.errors import UsageError
from jmap.cli.ids import Listing, Mailbox
from jmap.cli.output import OutputFormat, View, wire_of
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
SortOption = Annotated[
    str,
    typer.Option(
        help="Comma list: -date, subject, from, size, flagged.",
        autocompletion=comma_list(MAIL_SORT, signed=True),
    ),
]
LimitOption = Annotated[int, typer.Option("-n", "--limit", min=0, help="Messages per page.")]
PageOption = Annotated[int, typer.Option(min=1, help="Which page of --limit messages.")]
AllOption = Annotated[bool, typer.Option("--all", help="Every match, page after page.")]
CountOption = Annotated[bool, typer.Option("--count", help="Print the number of matches.")]
ThreadsOption = Annotated[bool, typer.Option("--threads", help="One row per conversation.")]
AnchorOption = Annotated[
    str | None, typer.Option(help="Start the page at this message: a listing number or an id.")
]
OffsetOption = Annotated[
    int, typer.Option(help="Start this many messages after --anchor; negative is before.")
]
ColumnsOption = Annotated[
    str | None,
    typer.Option(help=f"Comma list: {', '.join(COLUMNS)}.", autocompletion=comma_list(COLUMNS)),
]


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
class Paging:
    """Which page of the matches, in which order, and how to show it."""

    sort: str = "-date"
    limit: int = PAGE
    page: int = 1
    every: bool = False
    count: bool = False
    threads: bool = False
    anchor: str | None = None
    anchor_offset: int = 0
    columns: str | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """One listing's rows, in query order, and what the server said about them."""

    rows: list[dict[str, Any]]
    first: int
    total: int | None
    snippets: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])


@app.command("list")
def list_(
    ctx: typer.Context,
    words: Words = None,
    mailbox: Annotated[
        str | None,
        typer.Option(
            "--in", help="Mailbox to list; the inbox by default.", autocompletion=mailboxes
        ),
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
    anchor: AnchorOption = None,
    anchor_offset: OffsetOption = 0,
    columns: ColumnsOption = None,
) -> None:
    """List messages, newest first: the inbox, or the mailbox --in names."""
    filters = Filters(sender, recipient, subject, unread, flagged, attachment, before, after)
    paging = Paging(sort, limit, page, every, count, threads, anchor, anchor_offset, columns)
    run_listing(cli(ctx), words, filters, Scope(mailbox, everywhere), paging)


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
    anchor: AnchorOption = None,
    anchor_offset: OffsetOption = 0,
    columns: ColumnsOption = None,
) -> None:
    """Search every mailbox, and highlight what matched.

    Terms: from: to: cc: subject: body: in: notin: is:unread|flagged|answered|draft
    tag: has:attachment before: after: larger: smaller: header:Name=value. Combine
    with OR, NOT or -term, and (parentheses). A bare word searches everything.
    """
    filters = Filters(sender, recipient, subject, unread, flagged, attachment, before, after)
    scope = Scope(None, everywhere=True, highlight=True)
    paging = Paging(sort, limit, page, every, count, threads, anchor, anchor_offset, columns)
    run_listing(cli(ctx), words, filters, scope, paging)


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
    paging: Paging,
) -> None:
    if paging.anchor is not None and (paging.every or paging.page != 1):
        raise UsageError("--anchor says where the page starts", hint="leave out --page and --all")
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
        "sort": parse_sort(paging.sort),
        "collapse_threads": paging.threads,
    }
    client = context.require("Email/query", "Email/get")
    if paging.count:
        with client.batch() as batch:
            handles = queue_list(batch, limit=0, **query)
        context.out.file.write(f"{handles.query.result.total}\n")
        return
    if paging.anchor is not None:
        query["anchor"] = select(context, [paging.anchor]).ids[0]
        query["anchor_offset"] = paging.anchor_offset
    highlight = (
        scope.highlight
        and needs_snippets(node)
        and client.capabilities.supports("SearchSnippet/get")
    )
    fetched = _fetch(context, query, paging, highlight)
    _show(context, fetched, listed, paging)
    context.cursor.record(
        Listing(
            ids=tuple(str(row["id"]) for row in fetched.rows),
            first=fetched.first,
            mailbox_id=str(listed["id"]) if listed else None,
            query=text,
        )
    )


def _fetch(context: CLIContext, query: dict[str, Any], paging: Paging, highlight: bool) -> Fetched:
    """The page asked for, or with ``--all`` every page."""
    client = context.client()
    position = (paging.page - 1) * paging.limit
    rows: list[dict[str, Any]] = []
    snippets: dict[str, dict[str, Any]] = {}
    while True:
        with client.batch() as batch:
            handles = queue_list(
                batch,
                position=position + len(rows),
                limit=ALL_PAGE if paging.every else paging.limit,
                calculate_total=not paging.every,
                snippets=highlight,
                **query,
            )
        result = handles.query.result
        found = {item["id"]: item for item in wire_of(handles.emails.result)["list"]}
        rows += [found[email_id] for email_id in result.ids if email_id in found]
        if handles.snippets is not None:
            for snippet in wire_of(handles.snippets.result)["list"]:
                snippets[snippet["emailId"]] = snippet
        if not paging.every:
            # The server's position: with --anchor, only it knows where the page starts.
            return Fetched(rows, result.position + 1, result.total, snippets)
        if not result.ids:
            return Fetched(rows, position + 1, len(rows), snippets)


def _show(context: CLIContext, fetched: Fetched, listed: Mailbox | None, paging: Paging) -> None:
    builder = ListingColumns(
        table=context.renderer.format is OutputFormat.TABLE, snippets=fetched.snippets
    )
    columns = builder.named(paging.columns) if paging.columns else builder.default(listed)
    note = footer(fetched.rows, fetched.first, fetched.total, listed, paging.sort)
    context.renderer.render(View(fetched.rows, columns, first_number=fetched.first, footer=note))


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
