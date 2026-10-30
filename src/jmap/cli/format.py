"""Small conversions from wire values to the text a table shows."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from rich.markup import escape

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Glyph, and the keyword whose presence (or absence, for N) it shows.
_GLYPHS: Final = (
    ("N", "$seen", False),
    ("*", "$flagged", True),
    ("R", "$answered", True),
    ("F", "$forwarded", True),
    ("D", "$draft", True),
    ("J", "$junk", True),
)
_MARK: Final = re.compile(r"<mark>(.*?)</mark>", re.DOTALL)
_UNITS: Final = ("B", "K", "M", "G")


def flags(email: Mapping[str, Any]) -> str:
    """``N`` unread, ``*`` flagged, ``@`` attachment, ``R``/``F`` answered/forwarded,
    ``D`` draft, ``J`` junk."""
    keywords: Mapping[str, Any] = email.get("keywords") or {}
    glyphs: list[str] = []
    for glyph, keyword, wanted in _GLYPHS:
        if bool(keywords.get(keyword)) == wanted:
            glyphs.append(glyph)
        if glyph == "*" and email.get("hasAttachment"):
            glyphs.append("@")
    return "".join(glyphs)


def when(value: str | None, *, now: datetime) -> str:
    """A UTC date as people read it: the time today, the day this year, else the date."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(now.tzinfo)
    except ValueError:
        return value
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if moment.year == now.year:
        return moment.strftime("%b %d")
    return moment.strftime("%Y-%m-%d")


def size(count: int | None) -> str:
    """``1.2K``, ``340B``, ``3.0M``."""
    if count is None:
        return ""
    value, unit = float(count), 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value, unit = value / 1024, unit + 1
    return f"{count}B" if unit == 0 else f"{value:.1f}{_UNITS[unit]}"


def address(addresses: Any) -> str:
    """The first sender or recipient, by name if it has one; ``+N`` for the rest."""
    if not addresses:
        return ""
    first = addresses[0]
    label = first.get("name") or first.get("email") or ""
    return label + (f" +{len(addresses) - 1}" if len(addresses) > 1 else "")


def address_list(addresses: Any) -> str:
    """Every address in full: ``Name <email>, other@example.com``."""
    items: list[Mapping[str, Any]] = addresses or []
    return ", ".join(_full(item) for item in items)


def snippet_markup(text: str | None) -> str:
    """A search snippet as rich markup, its ``<mark>`` spans bold and underlined."""
    if not text:
        return ""
    parts = _MARK.split(text)
    return "".join(
        f"[bold underline]{escape(html.unescape(part))}[/]"
        if index % 2
        else escape(html.unescape(part))
        for index, part in enumerate(parts)
    )


def snippet_plain(text: str | None) -> str:
    return html.unescape(_MARK.sub(r"\1", text or ""))


def _full(item: Mapping[str, Any]) -> str:
    name, email = item.get("name"), item.get("email") or ""
    return f"{name} <{email}>" if name else email
