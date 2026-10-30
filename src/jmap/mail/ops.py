"""Batch recipes for the common mail reads.

Each function queues calls on a batch you already hold and returns the handles,
so a recipe shares its request with whatever else you queue, and the sync and
async clients use the same code. Nothing here sends anything.

```python
with client.batch() as batch:
    found = queue_list(batch, filter={"inMailbox": inbox_id}, limit=20)
for email in found.emails.result.items:
    print(email.subject)
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from jmap.core.patch import keyword_patch
from jmap.models.mail.headers import property_names, text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from jmap.core.ids import Id
    from jmap.core.invocation import Handle

#: What a message listing shows: enough for a row, nothing from the body.
LIST_PROPERTIES: Final = (
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "size",
    "receivedAt",
    "from",
    "to",
    "subject",
    "preview",
    "hasAttachment",
)

#: What reading a message needs: the headers people look at, and the body.
READ_PROPERTIES: Final = (
    *LIST_PROPERTIES,
    "blobId",
    "cc",
    "bcc",
    "replyTo",
    "sentAt",
    "messageId",
    "inReplyTo",
    "references",
    "textBody",
    "htmlBody",
    "attachments",
    "bodyValues",
)

#: Large enough for nearly every message, small enough not to fetch a novel.
DEFAULT_MAX_BODY_BYTES: Final = 1024 * 1024


class Queue(Protocol):
    """Anything calls can be queued on: a client's batch, sync or async."""

    def add(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        account_id: Id | None = None,
    ) -> Handle[Any]: ...


@dataclass(frozen=True, slots=True)
class ListHandles:
    #: ``Email/query``: ids, position and (when asked) the total.
    query: Handle[Any]
    #: ``Email/get`` of those ids.
    emails: Handle[Any]
    #: ``SearchSnippet/get`` for the same filter, when asked for.
    snippets: Handle[Any] | None = None


@dataclass(frozen=True, slots=True)
class ReadHandles:
    emails: Handle[Any]
    #: The ``Email/set`` that marked them seen, when asked to.
    seen: Handle[Any] | None = None


@dataclass(frozen=True, slots=True)
class ThreadHandles:
    #: ``Email/get`` of the starting message, for its ``threadId``.
    start: Handle[Any]
    #: ``Thread/get``: the thread's ``emailIds``, oldest first.
    thread: Handle[Any]
    #: ``Email/get`` of every message in the thread.
    emails: Handle[Any]


def queue_list(
    batch: Queue,
    *,
    filter: Mapping[str, Any] | None = None,  # the wire name
    sort: Sequence[Mapping[str, Any]] = (),
    position: int = 0,
    limit: int | None = None,
    calculate_total: bool = True,
    collapse_threads: bool = False,
    properties: Sequence[str] = LIST_PROPERTIES,
    snippets: bool = False,
) -> ListHandles:
    """``Email/query`` then ``Email/get`` of its ids, in one request.

    With ``snippets``, ``SearchSnippet/get`` highlights the matches too; it is
    worth asking only when ``filter`` searches text.
    """
    arguments: dict[str, Any] = {
        "position": position,
        "calculateTotal": calculate_total,
        "collapseThreads": collapse_threads,
    }
    if filter:
        arguments["filter"] = dict(filter)
    if sort:
        arguments["sort"] = [dict(comparator) for comparator in sort]
    if limit is not None:
        arguments["limit"] = limit
    query = batch.add("Email/query", arguments)
    emails = batch.add("Email/get", {"ids": query.ref_ids(), "properties": list(properties)})
    found = None
    if snippets:
        snippet_arguments: dict[str, Any] = {"emailIds": query.ref_ids()}
        if filter:
            snippet_arguments["filter"] = dict(filter)
        found = batch.add("SearchSnippet/get", snippet_arguments)
    return ListHandles(query, emails, found)


def queue_read(
    batch: Queue,
    email_ids: Iterable[str],
    *,
    headers: Iterable[str] = (),
    all_headers: bool = False,
    mark_seen: bool = True,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    properties: Sequence[str] = READ_PROPERTIES,
) -> ReadHandles:
    """``Email/get`` with the bodies, then ``Email/set`` marking them ``$seen``.

    The get comes first, so the result shows whether each message was unread.
    Leave ``mark_seen`` off for a read-only account; the batch refuses to queue
    a write there.
    """
    ids = list(email_ids)
    wanted = [*properties, *property_names(text(name) for name in headers)]
    if all_headers:
        wanted.append("headers")
    emails = batch.add(
        "Email/get",
        {
            "ids": ids,
            "properties": wanted,
            "fetchTextBodyValues": True,
            "fetchHTMLBodyValues": True,
            "maxBodyValueBytes": max_body_bytes,
        },
    )
    seen = None
    if mark_seen and ids:
        patch = keyword_patch(add=["$seen"])
        seen = batch.add("Email/set", {"update": dict.fromkeys(ids, patch)})
    return ReadHandles(emails, seen)


def queue_thread(
    batch: Queue, email_id: str, *, properties: Sequence[str] = LIST_PROPERTIES
) -> ThreadHandles:
    """Every message in ``email_id``'s thread, in one request.

    The last call reads ``/list/*/emailIds``, which RFC 8620 §3.7 flattens to a
    single list of ids. A server that gets the flattening wrong answers
    ``invalidResultReference``; then make two requests, ``Thread/get`` first.
    """
    start = batch.add("Email/get", {"ids": [email_id], "properties": ["threadId"]})
    thread = batch.add("Thread/get", {"ids": start.ref_list("threadId")})
    emails = batch.add(
        "Email/get", {"ids": thread.ref_list("emailIds"), "properties": list(properties)}
    )
    return ThreadHandles(start, thread, emails)
