"""Which messages a command means: listing numbers, ids, Message-IDs and threads."""

from __future__ import annotations

from dataclasses import dataclass

from jmap.cli.context import CLIContext
from jmap.cli.errors import NotFoundError
from jmap.cli.ids import Ref, parse_refs
from jmap.cli.output import wire_of
from jmap.core.ids import parse_id


@dataclass(frozen=True, slots=True)
class Selection:
    """The messages a command acts on."""

    ids: list[str]
    #: The mailbox they were listed from, when every one came from the listing.
    mailbox_id: str | None = None


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
