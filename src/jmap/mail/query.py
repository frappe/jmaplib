"""A search language for email, compiled to an RFC 8621 filter tree.

``from:bob is:unread after:7d (invoice OR receipt) -has:attachment`` becomes the
``Email/query`` filter a server understands. Terms next to each other are ANDed;
``OR`` and ``NOT`` (or a leading ``-``) combine them; parentheses group. A word
with no ``key:`` searches everywhere, as ``text:`` does.

Parsing and compiling are separate steps. :func:`parse` needs nothing; compiling
needs a way to turn a mailbox name into an id and a clock for relative dates,
which the caller supplies, so this module does no I/O.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Final

from jmap.core.errors import JMAPError
from jmap.core.patch import InvalidKeywordError, parse_keyword

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


class QueryError(JMAPError, ValueError):
    """The search text cannot be parsed or compiled."""


@dataclass(frozen=True, slots=True)
class Term:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class And:
    items: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Or:
    items: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Not:
    item: Node


Node = Term | And | Or | Not

#: Keys whose value is matched as text, and the filter condition each sets.
_TEXT_KEYS: Final = {k: k for k in ("text", "from", "to", "cc", "bcc", "subject", "body")}

#: ``is:`` values: the keyword each tests, and whether it must be present.
_IS: Final = {
    "unread": ("$seen", False),
    "read": ("$seen", True),
    "seen": ("$seen", True),
    "flagged": ("$flagged", True),
    "unflagged": ("$flagged", False),
    "answered": ("$answered", True),
    "forwarded": ("$forwarded", True),
    "draft": ("$draft", True),
    "junk": ("$junk", True),
    "phishing": ("$phishing", True),
}

_THREAD: Final = {
    "all": "allInThreadHaveKeyword",
    "some": "someInThreadHaveKeyword",
    "none": "noneInThreadHaveKeyword",
}

#: RFC 9219 §4.2.
_SMIME: Final = {
    "any": "hasSmime",
    "verified": "hasVerifiedSmime",
    "verified-at-delivery": "hasVerifiedSmimeAtDelivery",
}

#: Other spellings, and what they mean.
_ALIASES: Final = {"until": "before", "since": "after", "keyword": "tag"}

KEYS: Final = frozenset(
    {
        *_TEXT_KEYS,
        *_ALIASES,
        "in",
        "notin",
        "is",
        "tag",
        "has",
        "before",
        "after",
        "larger",
        "smaller",
        "header",
        "thread",
        "smime",
    }
)

#: Keys that search text, which is what ``SearchSnippet/get`` can highlight.
SNIPPET_KEYS: Final = frozenset(_TEXT_KEYS)

#: The ``sort`` names this language accepts, and the comparator property.
_SORT: Final = {
    "date": "receivedAt",
    "receivedat": "receivedAt",
    "sentat": "sentAt",
    "size": "size",
    "from": "from",
    "to": "to",
    "subject": "subject",
}

_TOKEN: Final = re.compile(
    r"""\s*(?:
        (?P<open>\() | (?P<close>\)) |
        (?P<word>-?(?:[A-Za-z][\w-]*:)?(?:"[^"]*"|[^\s()"]+)?)
    )""",
    re.VERBOSE,
)
_RELATIVE: Final = re.compile(r"(\d+)([dwmy])")
_SIZE: Final = re.compile(r"(\d+(?:\.\d+)?)([kmg]?)b?")
_UNITS: Final = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_DAYS: Final = {"d": 1, "w": 7, "m": 30, "y": 365}


def parse(text: str) -> Node | None:
    """The tree for ``text``, or ``None`` when it holds no terms."""
    return _Parser(list(_tokens(text))).parse()


def to_filter(
    node: Node,
    *,
    mailbox_id: Callable[[str], str],
    now: datetime | None = None,
    zone: tzinfo = UTC,
) -> dict[str, Any]:
    """Compile ``node`` to an ``Email/query`` filter.

    ``mailbox_id`` turns what ``in:`` names into an id. ``now`` and ``zone`` fix
    what ``today`` and ``7d`` mean; dates go to the server in UTC.
    """
    clock = now or datetime.now(zone)
    return _Compiler(mailbox_id, clock.astimezone(zone), zone).node(node)


def needs_snippets(node: Node | None) -> bool:
    """Whether the query searches text that ``SearchSnippet/get`` can highlight."""
    return any(term.key in SNIPPET_KEYS for term in _terms(node))


def mentions(node: Node | None, *keys: str) -> bool:
    """Whether any term in ``node`` uses one of ``keys``."""
    return any(term.key in keys for term in _terms(node))


def parse_date(text: str, *, now: datetime, zone: tzinfo = UTC) -> datetime:
    """When ``text`` means: ``2025-01-31``, ``2025-01-31T09:00``, ``today``, ``yesterday``.

    Or a span back from ``now``: ``7d``, ``2w``, ``3m``, ``1y``. A month is 30
    days and a year 365: these are for "recently", not calendars.
    """
    word = text.strip().lower()
    local = now.astimezone(zone)
    midnight = datetime.combine(local.date(), time(), zone)
    if word == "today":
        return midnight
    if word == "yesterday":
        return midnight - timedelta(days=1)
    relative = _RELATIVE.fullmatch(word)
    if relative:
        return local - timedelta(days=int(relative[1]) * _DAYS[relative[2]])
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        raise QueryError(f"{text!r} is not a date: use 2025-01-31, today, or 7d") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


def parse_size(text: str) -> int:
    """Bytes in ``text``: ``2048``, ``10K``, ``1.5M``, ``1G``."""
    found = _SIZE.fullmatch(text.strip().lower())
    if not found:
        raise QueryError(f"{text!r} is not a size: use 500, 10K, 2M or 1G")
    return int(float(found[1]) * _UNITS[found[2]])


def parse_sort(text: str) -> list[dict[str, Any]]:
    """``-date,subject`` as ``Email/query`` comparators. ``-`` sorts descending.

    ``flagged`` and ``unread`` put flagged or unread mail first.
    """
    comparators: list[dict[str, Any]] = []
    for part in filter(None, (piece.strip() for piece in text.split(","))):
        descending = part.startswith("-")
        name = part.lstrip("-+").lower()
        if name in {"flagged", "unread"}:
            keyword = "$flagged" if name == "flagged" else "$seen"
            # hasKeyword sorts false before true, so "flagged first" descends
            # and "unread first" (not $seen) ascends.
            ascending = (name == "unread") != descending
            comparators.append(
                {"property": "hasKeyword", "keyword": keyword, "isAscending": ascending}
            )
            continue
        if name not in _SORT:
            raise QueryError(f"cannot sort by {name!r}{_suggest(name, [*_SORT, 'flagged'])}")
        comparators.append({"property": _SORT[name], "isAscending": not descending})
    return comparators


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> Iterator[str]:
    position = 0
    while position < len(text):
        found = _TOKEN.match(text, position)
        # The pattern matches empty, so it stalls only on a '"' it cannot close.
        if found is None or found.end() == position:
            raise QueryError(f"unbalanced quote in {text!r}")
        position = found.end()
        token = found["open"] or found["close"] or found["word"]
        if token:
            yield token


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._at = 0

    def parse(self) -> Node | None:
        if not self._tokens:
            return None
        node = self._or()
        if self._at < len(self._tokens):
            raise QueryError(f"unexpected {self._tokens[self._at]!r}")
        return node

    def _peek(self) -> str | None:
        return self._tokens[self._at] if self._at < len(self._tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise QueryError("the query ends too early")
        self._at += 1
        return token

    def _or(self) -> Node:
        items = [self._and()]
        while self._peek() == "OR":
            self._take()
            items.append(self._and())
        return items[0] if len(items) == 1 else Or(tuple(items))

    def _and(self) -> Node:
        items = [self._unary()]
        while self._peek() not in {None, ")", "OR"}:
            if self._peek() == "AND":
                self._take()
            items.append(self._unary())
        return items[0] if len(items) == 1 else And(tuple(items))

    def _unary(self) -> Node:
        token = self._take()
        if token == "NOT":
            return Not(self._unary())
        if token == "(":
            node = self._or()
            if self._take() != ")":
                raise QueryError("a '(' is never closed")
            return node
        if token in {")", "OR", "AND"}:
            raise QueryError(f"unexpected {token!r}")
        if token.startswith("-") and len(token) > 1:
            return Not(_term(token[1:]))
        return _term(token)


def _term(word: str) -> Term:
    key, colon, value = word.partition(":")
    if not colon or key.startswith('"'):
        return Term("text", _unquote(word))
    key = key.lower()
    if key not in KEYS:
        hint = _suggest(key, KEYS) or ' (put it in "quotes" to search for the text)'
        raise QueryError(f"unknown search key {key!r}{hint}")
    if not value:
        raise QueryError(f"{key}: needs a value")
    return Term(_ALIASES.get(key, key), _unquote(value))


def _unquote(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text


def _terms(node: Node | None) -> Iterator[Term]:
    if isinstance(node, Term):
        yield node
    elif isinstance(node, Not):
        yield from _terms(node.item)
    elif node is not None:
        for item in node.items:
            yield from _terms(item)


def _suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, sorted(choices), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


# --------------------------------------------------------------------------- #
# Compiling
# --------------------------------------------------------------------------- #
class _Compiler:
    def __init__(self, mailbox_id: Callable[[str], str], now: datetime, zone: tzinfo) -> None:
        self._mailbox_id = mailbox_id
        self._now = now
        self._zone = zone

    def node(self, node: Node) -> dict[str, Any]:
        if isinstance(node, Term):
            return self.term(node, negated=False)
        if isinstance(node, Not):
            if isinstance(node.item, Term) and _foldable(node.item):
                return self.term(node.item, negated=True)
            return {"operator": "NOT", "conditions": [self.node(node.item)]}
        conditions = [self.node(item) for item in node.items]
        if isinstance(node, Or):
            return {"operator": "OR", "conditions": conditions}
        return _merge(conditions)

    def term(self, term: Term, *, negated: bool) -> dict[str, Any]:
        key, value = term.key, term.value
        if key in _TEXT_KEYS:
            return {_TEXT_KEYS[key]: value}
        if key in {"is", "tag"}:
            keyword, present = _is(value) if key == "is" else (_keyword(value), True)
            return {"hasKeyword" if present != negated else "notKeyword": keyword}
        if key == "has":
            if value.lower() != "attachment":
                raise QueryError(f"unknown has:{value} (only has:attachment)")
            return {"hasAttachment": not negated}
        return self._other(key, value)

    def _other(self, key: str, value: str) -> dict[str, Any]:
        if key == "in":
            return {"inMailbox": self._mailbox_id(value)}
        if key == "notin":
            return {"inMailboxOtherThan": [self._mailbox_id(value)]}
        if key in {"before", "after"}:
            when = parse_date(value, now=self._now, zone=self._zone)
            return {key: when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if key in {"larger", "smaller"}:
            return {"minSize" if key == "larger" else "maxSize": parse_size(value)}
        if key == "header":
            name, equals, wanted = value.partition("=")
            return {"header": [name, wanted] if equals else [name]}
        if key == "thread":
            scope, _, keyword = value.partition(":")
            if scope.lower() not in _THREAD or not keyword:
                raise QueryError(
                    f"use thread:all:KEYWORD, thread:some: or thread:none:, not {value}"
                )
            return {_THREAD[scope.lower()]: _keyword(keyword)}
        if value.lower() not in _SMIME:
            raise QueryError("use smime:any, smime:verified or smime:verified-at-delivery")
        return {_SMIME[value.lower()]: True}


def _foldable(term: Term) -> bool:
    """Whether NOT over ``term`` is a single condition rather than an operator."""
    return term.key in {"is", "tag", "has"}


def _is(value: str) -> tuple[str, bool]:
    found = _IS.get(value.lower())
    if found is None:
        raise QueryError(f"unknown is:{value}{_suggest(value.lower(), _IS)}")
    return found


def _keyword(value: str) -> str:
    name = value.lower()
    if name in {"flagged", "seen", "answered", "forwarded", "draft", "junk", "notjunk"}:
        name = f"${name}"
    try:
        return parse_keyword(name)
    except InvalidKeywordError as error:
        raise QueryError(str(error)) from error


def _merge(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    """AND as one condition where the keys allow it, else as an operator.

    One ``FilterCondition`` with several properties is already an AND (RFC 8621
    §4.4.1), and it is the form every server handles best.
    """
    merged: dict[str, Any] = {}
    rest: list[dict[str, Any]] = []
    for condition in conditions:
        if "operator" in condition or merged.keys() & condition.keys():
            rest.append(condition)
        else:
            merged.update(condition)
    if not rest:
        return merged
    return {"operator": "AND", "conditions": [merged, *rest] if merged else rest}
