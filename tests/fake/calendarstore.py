"""An in-memory calendar store behind the fake server, for driving the calendar commands.

Implements enough of draft-ietf-jmap-calendars to be honest about what the
commands send: windows of local time, expanded occurrences with their own ids,
overrides keyed by recurrence id, patches applied path by path, and free/busy.
It is test code; correctness beyond that is not a goal.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from jmap.capabilities.calendars import (
    AVAILABILITY_URN,
    CALENDARS_PARSE_URN,
    CALENDARS_URN,
    duration_seconds,
)

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

PRINCIPALS_URN = "urn:ietf:params:jmap:principals"

CALENDARS: list[dict[str, Any]] = [
    {"id": "c1", "name": "Personal", "isDefault": True, "color": "blue", "timeZone": None},
    {"id": "c2", "name": "Work", "isDefault": False, "color": "red", "timeZone": "Europe/London"},
]

_STEPS = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


def event(
    event_id: str,
    title: str,
    start: str,
    *,
    calendar: str = "c1",
    duration: str = "PT1H",
    zone: str | None = "Europe/London",
    **extra: Any,
) -> dict[str, Any]:
    stored = {
        "id": event_id,
        "calendarIds": {calendar: True},
        "title": title,
        "start": start,
        "duration": duration,
        **extra,
    }
    if zone is not None:
        stored["timeZone"] = zone
    return stored


class CalendarStore:
    """Calendars, events and notifications, answering the calendar methods of ``server``."""

    def __init__(self, server: FakeJMAPServer, *, expand_limit: str | None = "P30D") -> None:
        self.server = server
        self.calendars = {item["id"]: dict(item) for item in CALENDARS}
        self.events: dict[str, dict[str, Any]] = {}
        self.notifications: dict[str, dict[str, Any]] = {}
        self.identities: list[dict[str, Any]] = [
            {"id": "pi1", "name": "Alice", "calendarAddress": "mailto:alice@example.com"}
        ]
        self.principals = {"p-bob": "bob@example.com", "p-room": "room@example.com"}
        self.busy: list[dict[str, Any]] = []
        #: What each ``CalendarEvent/query`` was asked, for assertions.
        self.queries: list[dict[str, Any]] = []
        self.last_set: dict[str, Any] = {}
        self.last_availability: dict[str, Any] = {}
        self._created = 0
        limits = {} if expand_limit is None else {"maxExpandedQueryDuration": expand_limit}
        account_capabilities = {
            CALENDARS_URN: limits,
            CALENDARS_PARSE_URN: {},
            AVAILABILITY_URN: {"maxAvailabilityDuration": "P60D"},
            PRINCIPALS_URN: {},
        }
        for urn in account_capabilities:
            server.capabilities[urn] = {}
            server.primary_accounts[urn] = "a"
        server.accounts["a"]["accountCapabilities"] = account_capabilities
        for name, handler in {
            "Calendar/get": self.calendar_get,
            "Calendar/set": self.calendar_set,
            "CalendarEvent/query": self.event_query,
            "CalendarEvent/get": self.event_get,
            "CalendarEvent/set": self.event_set,
            "CalendarEvent/parse": self.event_parse,
            "ParticipantIdentity/get": self.identity_get,
            "CalendarEventNotification/query": self.notification_query,
            "CalendarEventNotification/get": self.notification_get,
            "CalendarEventNotification/set": self.notification_set,
            "Principal/query": self.principal_query,
            "Principal/getAvailability": self.availability,
        }.items():
            server.handle(name, handler)

    def add(self, *events: dict[str, Any]) -> None:
        for item in events:
            self.events[item["id"]] = item

    # -- calendars ---------------------------------------------------------- #
    def calendar_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return _got(args, self.calendars)

    def calendar_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        for key, item in (args.get("create") or {}).items():
            new_id = self._new_id("cal")
            self.calendars[new_id] = {"id": new_id, "isDefault": False, **item}
            created[key] = {"id": new_id}
        default = args.get("onSuccessSetIsDefault")
        if default:
            chosen = created[default[1:]]["id"] if default.startswith("#") else default
            for calendar in self.calendars.values():
                calendar["isDefault"] = calendar["id"] == chosen
        for key, patch in (args.get("update") or {}).items():
            self.calendars[key].update(patch)
        destroyed: list[str] = []
        not_destroyed: dict[str, Any] = {}
        for key in args.get("destroy") or []:
            inside = [e for e in self.events.values() if key in e["calendarIds"]]
            if inside and not args.get("onDestroyRemoveEvents"):
                not_destroyed[key] = {"type": "calendarHasEvent"}
                continue
            for item in inside:
                del self.events[item["id"]]
            del self.calendars[key]
            destroyed.append(key)
        return {
            "accountId": args["accountId"],
            "created": created,
            "updated": dict.fromkeys(args.get("update") or {}),
            "destroyed": destroyed,
            "notDestroyed": not_destroyed,
        }

    # -- events ------------------------------------------------------------- #
    def event_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        self.queries.append(args)
        condition = args.get("filter") or {}
        after = datetime.fromisoformat(condition["after"])
        before = datetime.fromisoformat(condition["before"])
        wanted = condition.get("inCalendars")
        ids: list[str] = []
        for item in self.events.values():
            if wanted is not None and not set(item["calendarIds"]) & set(wanted):
                continue
            for occurrence in self._occurrences(item, expand=args.get("expandRecurrences")):
                begins = datetime.fromisoformat(occurrence["start"])
                ends = begins + timedelta(seconds=duration_seconds(occurrence["duration"]) or 0)
                if begins < before and ends > after:
                    ids.append(occurrence["id"])
        return {
            "accountId": args["accountId"],
            "queryState": "q",
            "canCalculateChanges": False,
            "position": 0,
            "ids": ids,
        }

    def event_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        wanted = args.get("properties")
        found: list[dict[str, Any]] = []
        missing: list[str] = []
        for event_id in args.get("ids") or []:
            item = self._find(event_id)
            if item is None:
                missing.append(event_id)
                continue
            if item.get("timeZone"):
                item = {**item, "utcStart": _utc(item["start"], item["timeZone"])}
            found.append(
                {k: v for k, v in item.items() if wanted is None or k in wanted or k == "id"}
            )
        return {"accountId": args["accountId"], "state": "s", "list": found, "notFound": missing}

    def event_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        created: dict[str, Any] = {}
        for key, item in (args.get("create") or {}).items():
            new_id = self._new_id("ev")
            self.events[new_id] = {"id": new_id, **item}
            created[key] = {"id": new_id, "uid": f"uid-{new_id}"}
        updated: dict[str, Any] = {}
        not_updated: dict[str, Any] = {}
        for key, patch in (args.get("update") or {}).items():
            if key not in self.events:
                not_updated[key] = {"type": "notFound"}
                continue
            _apply(self.events[key], patch)
            updated[key] = None
        destroyed = [key for key in args.get("destroy") or [] if self.events.pop(key, None)]
        self.last_set = args
        return {
            "accountId": args["accountId"],
            "created": created,
            "updated": updated,
            "notUpdated": not_updated,
            "destroyed": destroyed,
        }

    def event_parse(self, args: dict[str, Any], server: FakeJMAPServer) -> dict[str, Any]:
        parsed: dict[str, list[dict[str, Any]]] = {}
        for blob_id in args["blobIds"]:
            text = server.blobs[blob_id][0].decode()
            summaries = [line[8:] for line in text.splitlines() if line.startswith("SUMMARY:")]
            parsed[blob_id] = [
                {"@type": "Event", "id": "x", "uid": f"u{n}", "title": title, "start": START}
                for n, title in enumerate(summaries)
            ]
        return {"accountId": args["accountId"], "parsed": parsed}

    def identity_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return {"accountId": args["accountId"], "state": "s", "list": self.identities}

    # -- notifications, principals, availability ---------------------------- #
    def notification_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return {
            "accountId": args["accountId"],
            "queryState": "q",
            "canCalculateChanges": False,
            "position": 0,
            "ids": list(self.notifications),
        }

    def notification_get(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        return _got(args, self.notifications)

    def notification_set(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        destroyed = [key for key in args.get("destroy") or [] if self.notifications.pop(key, None)]
        return {"accountId": args["accountId"], "destroyed": destroyed}

    def principal_query(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        email = (args.get("filter") or {}).get("email")
        ids = [key for key, address in self.principals.items() if address == email]
        return {
            "accountId": args["accountId"],
            "queryState": "q",
            "canCalculateChanges": False,
            "position": 0,
            "ids": ids,
        }

    def availability(self, args: dict[str, Any], _server: FakeJMAPServer) -> dict[str, Any]:
        self.last_availability = args
        return {"accountId": args["accountId"], "list": self.busy}

    # -- helpers ------------------------------------------------------------ #
    def _new_id(self, prefix: str) -> str:
        self._created += 1
        return f"{prefix}{self._created}"

    def _occurrences(self, item: dict[str, Any], *, expand: Any) -> list[dict[str, Any]]:
        rule = item.get("recurrenceRule")
        if not rule or not expand:
            return [item]
        step = _STEPS[rule["frequency"]]
        first = datetime.fromisoformat(item["start"])
        overrides = item.get("recurrenceOverrides") or {}
        found: list[dict[str, Any]] = []
        for number in range(rule.get("count", 10)):
            key = (first + number * step).isoformat()
            override = overrides.get(key) or {}
            if override.get("excluded"):
                continue
            occurrence = {
                **copy.deepcopy(item),
                "id": f"{item['id']}.{key}",
                "baseEventId": item["id"],
                "recurrenceId": key,
                "start": key,
                "recurrenceRule": None,
                "recurrenceOverrides": None,
                **override,
            }
            found.append(occurrence)
        return found

    def _find(self, event_id: str) -> dict[str, Any] | None:
        if event_id in self.events:
            return self.events[event_id]
        base = self.events.get(event_id.split(".", 1)[0])
        if base is None:
            return None
        every = self._occurrences(base, expand=True)
        return next((item for item in every if item["id"] == event_id), None)


START = "2026-11-02T10:00:00"


def _got(args: dict[str, Any], store: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = args.get("ids")
    wanted = args.get("properties")
    found = [
        {k: v for k, v in item.items() if wanted is None or k in wanted or k == "id"}
        for key, item in store.items()
        if ids is None or key in ids
    ]
    missing = [key for key in ids or [] if key not in store]
    return {"accountId": args["accountId"], "state": "s", "list": found, "notFound": missing}


def _apply(item: dict[str, Any], patch: dict[str, Any]) -> None:
    """A JMAP patch, its paths one or two levels deep."""
    for path, value in patch.items():
        head, _, rest = path.partition("/")
        if rest:
            item[head][rest] = value
        else:
            item[head] = value


def _utc(start: str, zone: str) -> str:
    wall = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(zone))
    return wall.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
