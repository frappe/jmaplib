"""Working with mail above the protocol: searching, reading, and the batches for both.

Everything here is I/O-free and works on a batch or a fetched email, so the sync
and async clients share it:

* :mod:`jmap.mail.query` - a search language (``from:bob is:unread after:7d``)
  compiled to an ``Email/query`` filter.
* :mod:`jmap.mail.render` - an email's body as readable text, HTML included.
* :mod:`jmap.mail.ops` - the batches behind listing, reading and threads.
"""

from __future__ import annotations

from jmap.mail.ops import (
    LIST_PROPERTIES,
    READ_PROPERTIES,
    ListHandles,
    ReadHandles,
    ThreadHandles,
    queue_list,
    queue_read,
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
    "ThreadHandles",
    "attachments_of",
    "body_text",
    "html_to_text",
    "parse",
    "parse_sort",
    "queue_list",
    "queue_read",
    "queue_thread",
    "quote",
    "to_filter",
]
