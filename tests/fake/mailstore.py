"""An in-memory mail store behind the fake server, for driving the mail commands.

Implements enough of RFC 8621 to be honest about what the commands send: the
filter conditions they compile to, patches applied key by key, threads, and
snippets. It is test code; correctness beyond that is not a goal.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from jmap.testing import FakeJMAPServer

if TYPE_CHECKING:
    from collections.abc import Callable

CORE_URN = "urn:ietf:params:jmap:core"
MAIL_URN = "urn:ietf:params:jmap:mail"

MAILBOXES: list[dict[str, Any]] = [
    {"id": "inbox", "name": "Inbox", "role": "inbox", "parentId": None, "sortOrder": 1},
    {"id": "arch", "name": "Archive", "role": "archive", "parentId": None, "sortOrder": 2},
    {"id": "trash", "name": "Trash", "role": "trash", "parentId": None, "sortOrder": 3},
    {"id": "junk", "name": "Junk", "role": "junk", "parentId": None, "sortOrder": 4},
    {"id": "sent", "name": "Sent", "role": "sent", "parentId": None, "sortOrder": 5},
    {"id": "y25", "name": "2025", "role": None, "parentId": "arch", "sortOrder": 0},
]


def email(
    email_id: str,
    subject: str,
    *,
    sender: str = "bob@example.com",
    received: str = "2026-10-01T09:00:00Z",
    mailboxes: tuple[str, ...] = ("inbox",),
    keywords: tuple[str, ...] = (),
    thread: str | None = None,
    body: str = "Hello there.",
    html: str | None = None,
    attachment: bool = False,
) -> dict[str, Any]:
    text_part = {"partId": "1", "type": "text/plain", "blobId": f"b-{email_id}-1", "size": 12}
    html_part = {"partId": "2", "type": "text/html", "blobId": f"b-{email_id}-2", "size": 40}
    files = (
        [
            {
                "partId": "3",
                "type": "application/pdf",
                "name": "invoice.pdf",
                "size": 2048,
                "blobId": f"b-{email_id}-3",
                "disposition": "attachment",
            }
        ]
        if attachment
        else []
    )
    values = {"1": {"value": body, "isTruncated": False, "isEncodingProblem": False}}
    if html is not None:
        values["2"] = {"value": html, "isTruncated": False, "isEncodingProblem": False}
    return {
        "id": email_id,
        "blobId": f"blob-{email_id}",
        "threadId": thread or f"t-{email_id}",
        "mailboxIds": dict.fromkeys(mailboxes, True),
        "keywords": dict.fromkeys(keywords, True),
        "size": 1000 + len(body),
        "receivedAt": received,
        "sentAt": received,
        "messageId": [f"{email_id}@example.com"],
        "from": [{"name": sender.split("@")[0].title(), "email": sender}],
        "to": [{"name": "Alice", "email": "alice@example.com"}],
        "cc": [],
        "subject": subject,
        "preview": body[:40],
        "hasAttachment": attachment,
        "textBody": [text_part],
        "htmlBody": [html_part if html is not None else text_part],
        "attachments": files,
        "bodyValues": values,
        "header:List-Id:asText": "<list.example.com>",
        "headers": [{"name": "Subject", "value": f" {subject}"}],
    }


class MailStore:
    """Mailboxes and emails, answering the mail methods of ``server``."""

    def __init__(self, server: FakeJMAPServer) -> None:
        self.mailboxes = {box["id"]: dict(box) for box in MAILBOXES}
        self.emails: dict[str, dict[str, Any]] = {}
        self._created = 0
        for name, handler in {
            "Mailbox/get": self.mailbox_get,
            "Mailbox/set": self.mailbox_set,
            "Email/query": self.email_query,
            "Email/get": self.email_get,
            "Email/set": self.email_set,
            "Thread/get": self.thread_get,
            "SearchSnippet/get": self.snippet_get,
        }.items():
            server.handle(name, handler)

    def add(self, *emails: dict[str, Any]) -> None:
        for item in emails:
            self.emails[item["id"]] = item

    # -- mailboxes ---------------------------------------------------------- #
    def mailbox_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        ids = args.get("ids")
        boxes = [
            self._counted(box) for key, box in self.mailboxes.items() if ids is None or key in ids
        ]
        return {
            "accountId": args["accountId"],
            "state": "s",
            "list": boxes,
            "notFound": [i for i in ids or [] if i not in self.mailboxes],
        }

    def _counted(self, box: dict[str, Any]) -> dict[str, Any]:
        inside = [e for e in self.emails.values() if box["id"] in e["mailboxIds"]]
        unread = [e for e in inside if "$seen" not in e["keywords"]]
        return {
            **box,
            "totalEmails": len(inside),
            "unreadEmails": len(unread),
            "isSubscribed": box.get("isSubscribed", True),
            "myRights": {"mayDelete": True},
        }

    def mailbox_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        updated: dict[str, Any] = {}
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        for key, box in (args.get("create") or {}).items():
            self._created += 1
            new_id = f"new{self._created}"
            self.mailboxes[new_id] = {"id": new_id, "role": None, "sortOrder": 9, **box}
            created[key] = {"id": new_id}
        for mailbox_id, patch in (args.get("update") or {}).items():
            self.mailboxes[mailbox_id].update(patch)
            updated[mailbox_id] = None
        for mailbox_id in args.get("destroy") or []:
            holding = [e for e in self.emails.values() if mailbox_id in e["mailboxIds"]]
            if holding and not args.get("onDestroyRemoveEmails"):
                not_destroyed[mailbox_id] = {"type": "mailboxHasEmail"}
                continue
            del self.mailboxes[mailbox_id]
            destroyed.append(mailbox_id)
        return {
            "accountId": args["accountId"],
            "created": created,
            "updated": updated,
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    # -- emails ------------------------------------------------------------- #
    def email_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        found = [e for e in self.emails.values() if _matches(e, args.get("filter"))]
        descending = not (args.get("sort") or [{"isAscending": False}])[0].get("isAscending", True)
        found.sort(key=lambda e: e["receivedAt"], reverse=descending)
        if args.get("collapseThreads"):
            first: dict[str, dict[str, Any]] = {}
            for item in found:
                first.setdefault(item["threadId"], item)
            found = list(first.values())
        position = args.get("position", 0)
        limit = args.get("limit")
        page = found[position : None if limit is None else position + limit]
        result = {
            "accountId": args["accountId"],
            "queryState": "q",
            "canCalculateChanges": False,
            "position": position,
            "ids": [e["id"] for e in page],
        }
        if args.get("calculateTotal"):
            result["total"] = len(found)
        return result

    def email_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        wanted = args.get("properties")
        found = [self.emails[i] for i in args.get("ids") or [] if i in self.emails]
        items = [
            {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if wanted is None or key in wanted or key == "id"
            }
            for item in found
        ]
        return {
            "accountId": args["accountId"],
            "state": "s",
            "list": items,
            "notFound": [i for i in args.get("ids") or [] if i not in self.emails],
        }

    def email_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        updated: dict[str, Any] = {}
        not_updated: dict[str, Any] = {}
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        for email_id, patch in (args.get("update") or {}).items():
            if email_id not in self.emails:
                not_updated[email_id] = {"type": "notFound", "description": "gone"}
                continue
            _apply(self.emails[email_id], patch)
            updated[email_id] = None
        for email_id in args.get("destroy") or []:
            if self.emails.pop(email_id, None) is None:
                not_destroyed[email_id] = {"type": "notFound"}
            else:
                destroyed.append(email_id)
        return {
            "accountId": args["accountId"],
            "updated": updated,
            "notUpdated": not_updated,
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    def thread_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        threads = []
        for thread_id in args.get("ids") or []:
            members = sorted(
                (e for e in self.emails.values() if e["threadId"] == thread_id),
                key=lambda e: e["receivedAt"],
            )
            if members:
                threads.append({"id": thread_id, "emailIds": [e["id"] for e in members]})
        found = {t["id"] for t in threads}
        return {
            "accountId": args["accountId"],
            "state": "s",
            "list": threads,
            "notFound": [i for i in args.get("ids") or [] if i not in found],
        }

    def snippet_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        word = str((args.get("filter") or {}).get("text") or "")
        snippets = []
        for email_id in args.get("emailIds") or []:
            subject = self.emails[email_id]["subject"]
            marked = subject.replace(word, f"<mark>{word}</mark>") if word else subject
            snippets.append({"emailId": email_id, "subject": marked, "preview": "a &amp; b"})
        return {"accountId": args["accountId"], "list": snippets, "notFound": []}


def _matches(item: dict[str, Any], condition: dict[str, Any] | None) -> bool:
    if not condition:
        return True
    operator = condition.get("operator")
    if operator:
        results = [_matches(item, sub) for sub in condition["conditions"]]
        return {"AND": all(results), "OR": any(results), "NOT": not any(results)}[operator]
    checks: dict[str, Callable[[Any], bool]] = {
        "inMailbox": lambda v: v in item["mailboxIds"],
        "inMailboxOtherThan": lambda v: any(m not in v for m in item["mailboxIds"]),
        "hasKeyword": lambda v: v in item["keywords"],
        "notKeyword": lambda v: v not in item["keywords"],
        "hasAttachment": lambda v: item["hasAttachment"] == v,
        "text": lambda v: v.lower() in (item["subject"] + item["bodyValues"]["1"]["value"]).lower(),
        "subject": lambda v: v.lower() in item["subject"].lower(),
        "from": lambda v: v.lower() in item["from"][0]["email"],
        "after": lambda v: item["receivedAt"] >= v,
        "before": lambda v: item["receivedAt"] < v,
        "header": lambda v: f"<{item['messageId'][0]}>" == v[1] or item["messageId"][0] == v[1],
    }
    return all(checks[key](value) for key, value in condition.items())


def _apply(item: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        name, _, sub = key.partition("/")
        if not sub:
            item[name] = value
        elif value is None:
            item[name].pop(sub, None)
        else:
            item[name][sub] = value


def mail_server() -> tuple[FakeJMAPServer, MailStore]:
    server = FakeJMAPServer(
        capabilities={CORE_URN: {"maxCallsInRequest": 16, "maxObjectsInSet": 500}, MAIL_URN: {}},
        primary_accounts={CORE_URN: "a", MAIL_URN: "a"},
    )
    return server, MailStore(server)
