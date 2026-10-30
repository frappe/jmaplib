"""Working with mail above the protocol: searching, reading, and the batches for both.

Everything here is I/O-free and works on a batch or a fetched email, so the sync
and async clients share it:

* :mod:`jmap.mail.query` - a search language (``from:bob is:unread after:7d``)
  compiled to an ``Email/query`` filter.
* :mod:`jmap.mail.render` - an email's body as readable text, HTML included.
* :mod:`jmap.mail.ops` - the batches behind listing, reading, threads and sending.
* :mod:`jmap.mail.compose` - a draft, its text form, and the ``Email/set`` it becomes.
* :mod:`jmap.mail.reply` - replies, forwards, and which identity sends them.
* :mod:`jmap.mail.addresses` - ``Name <email>`` lists, parsed and formatted.
"""

from __future__ import annotations

from jmap.mail.ops import (
    LIST_PROPERTIES,
    READ_PROPERTIES,
    ListHandles,
    ReadHandles,
    SendHandles,
    ThreadHandles,
    queue_import_and_send,
    queue_list,
    queue_read,
    queue_send,
    queue_thread,
)
from jmap.mail.query import QueryError, parse, parse_sort, to_filter
from jmap.mail.render import RenderedBody, attachments_of, body_text, html_to_text, quote

__all__ = [
    "LIST_PROPERTIES",
    "READ_PROPERTIES",
    "ListHandles",
    "QueryError",
    "ReadHandles",
    "RenderedBody",
    "SendHandles",
    "ThreadHandles",
    "attachments_of",
    "body_text",
    "html_to_text",
    "parse",
    "parse_sort",
    "queue_import_and_send",
    "queue_list",
    "queue_read",
    "queue_send",
    "queue_thread",
    "quote",
    "to_filter",
]
