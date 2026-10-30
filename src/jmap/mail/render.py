"""Turning a fetched email into text a person can read.

HTML is converted with the standard library's parser, so the library keeps its
dependencies to httpx and pydantic. It does the common structure well - blocks,
links, lists, quotes, preformatted text - and nested layout tables less well;
:func:`body_text` keeps the same signature should a better converter be wanted.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jmap.models.mail.objects import Email, EmailBodyPart

Source = Literal["text", "html", "preview", "none"]

#: Elements whose content never reaches the reader.
_HIDDEN: Final = frozenset({"script", "style", "head", "title", "template"})
#: Elements that start a new paragraph.
_BLOCKS: Final = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "table",
        "tr",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "address",
        "center",
        "form",
    }
)
#: Whitespace HTML collapses, the no-break space included: it is for layout.
_SPACES: Final = re.compile(r"[ \t\r\n\f\v\xa0]+")
_BLANK_LINES: Final = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class RenderedBody:
    text: str
    #: Which body the text came from: the plain part, the HTML part, the preview.
    source: Source
    #: The server cut a part short at ``maxBodyValueBytes``.
    truncated: bool = False
    #: The server could not decode a part's charset cleanly.
    encoding_problem: bool = False


def html_to_text(html: str, *, width: int | None = None) -> str:
    """Readable text from an HTML body.

    Blocks become paragraphs, list items ``- `` lines, quotes ``> `` lines, and
    links ``text <url>``. ``width`` wraps long lines, keeping quote markers.
    """
    converter = _Converter()
    converter.feed(html)
    converter.close()
    # Only newlines: a leading <pre> keeps its indentation.
    text = _BLANK_LINES.sub("\n\n", converter.text()).strip("\n")
    if width is None:
        return text
    return "\n".join(_wrap(line, width) for line in text.split("\n"))


def body_text(email: Email, *, prefer: Literal["text", "html"] = "text") -> RenderedBody:
    """The body of ``email`` as text, from the parts and values ``Email/get`` returned.

    Ask ``Email/get`` for ``textBody``/``htmlBody`` and ``bodyValues``, with
    ``fetchTextBodyValues``/``fetchHTMLBodyValues``. ``prefer="html"`` reads the
    HTML alternative where there is one. Without any body value the preview is
    used, and ``source`` says so.
    """
    order = (
        (email.html_body, email.text_body)
        if prefer == "html"
        else (email.text_body, email.html_body)
    )
    for parts in order:
        rendered = _render(email, parts or [])
        if rendered is not None:
            return rendered
    if email.preview:
        return RenderedBody(email.preview, "preview")
    return RenderedBody("", "none")


def attachments_of(email: Email) -> list[EmailBodyPart]:
    """The parts a person would call attachments.

    ``attachments`` when the server returned it; otherwise the leaves of
    ``bodyStructure`` that are not the text or HTML body.
    """
    if email.attachments is not None:
        return list(email.attachments)
    shown = {part.part_id for part in (*(email.text_body or ()), *(email.html_body or ()))}
    return [
        part
        for part in _leaves(email.body_structure)
        if part.part_id not in shown and _is_attachment(part)
    ]


def quote(text: str, prefix: str = "> ") -> str:
    """``text`` with ``prefix`` before every line, as a reply quotes it."""
    return "\n".join(f"{prefix}{line}".rstrip() for line in text.split("\n"))


def _render(email: Email, parts: list[EmailBodyPart]) -> RenderedBody | None:
    values = email.body_values or {}
    texts: list[str] = []
    truncated = problem = False
    source: Source = "none"
    for part in parts:
        value = values.get(part.part_id or "")
        if value is None or value.value is None:
            continue
        is_html = (part.type or "").lower() == "text/html"
        if not texts:
            source = "html" if is_html else "text"
        texts.append(html_to_text(value.value) if is_html else value.value.strip("\n"))
        truncated = truncated or bool(value.is_truncated)
        problem = problem or bool(value.is_encoding_problem)
    if not texts:
        return None
    return RenderedBody("\n\n".join(texts), source, truncated, problem)


def _leaves(part: EmailBodyPart | None) -> Iterator[EmailBodyPart]:
    if part is None:
        return
    if part.sub_parts:
        for sub in part.sub_parts:
            yield from _leaves(sub)
    else:
        yield part


def _is_attachment(part: EmailBodyPart) -> bool:
    if (part.disposition or "").lower() == "attachment":
        return True
    kind = (part.type or "").lower()
    return not kind.startswith("text/") and not kind.startswith("multipart/")


def _wrap(line: str, width: int) -> str:
    stripped = line.lstrip("> ")
    prefix = line[: len(line) - len(stripped)]
    if not stripped:
        return line
    return textwrap.fill(
        stripped,
        width=max(width - len(prefix), 20),
        initial_indent=prefix,
        subsequent_indent=prefix,
        break_long_words=False,
        break_on_hyphens=False,
    )


class _Converter(HTMLParser):
    """Collects text, tracking the few elements that change its shape.

    Line breaks are held as a pending gap and written only when more text
    follows, so separators appear between content and never trail it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._gap = 0
        #: The shallowest quote level seen while the gap was pending.
        self._gap_quote = 0
        self._hidden = 0
        self._pre = 0
        self._quote = 0
        self._links: list[str | None] = []
        self._lists: list[int | None] = []

    def text(self) -> str:
        return "".join(self._out)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag == "br":
            self._open_gap(self._gap + 1)
        elif tag in {"pre", "blockquote"}:
            self._paragraph()
            if tag == "pre":
                self._pre += 1
            else:
                self._quote += 1
        elif tag == "a":
            self._links.append(dict(attrs).get("href"))
        elif tag in {"ul", "ol"}:
            self._line() if self._lists else self._paragraph()
            self._lists.append(0 if tag == "ol" else None)
        elif tag == "li":
            self._list_item()
        elif tag in {"td", "th"}:
            self._emit(" ")
        elif tag in _BLOCKS:
            self._paragraph()

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN:
            self._hidden = max(self._hidden - 1, 0)
        elif tag in {"pre", "blockquote"}:
            if tag == "pre":
                self._pre = max(self._pre - 1, 0)
            else:
                self._quote = max(self._quote - 1, 0)
            self._paragraph()
        elif tag == "a" and self._links:
            href = self._links.pop()
            if href and not href.startswith(("mailto:", "#")) and href not in self._tail():
                self._emit(f" <{href}>")
        elif tag in {"ul", "ol"} and self._lists:
            self._lists.pop()
            self._line() if self._lists else self._paragraph()
        elif tag in _BLOCKS:
            self._paragraph()

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        if self._pre:
            for index, line in enumerate(data.split("\n")):
                if index:
                    self._open_gap(self._gap + 1)
                self._emit(line)
            return
        text = _SPACES.sub(" ", data)
        if self._at_line_start():
            text = text.lstrip()
        if text:
            self._emit(text)

    def _list_item(self) -> None:
        self._line()
        counter = self._lists[-1] if self._lists else None
        if counter is None:
            marker = "- "
        else:
            counter += 1
            self._lists[-1] = counter
            marker = f"{counter}. "
        self._emit("  " * max(len(self._lists) - 1, 0) + marker)

    def _emit(self, text: str) -> None:
        if self._gap and self._out:
            # Blank lines inside a quote keep its marker, so the quote stays one block.
            blank = ">" * self._gap_quote + "\n"
            self._out.append("\n" + blank * (self._gap - 1))
        self._gap = 0
        if self._quote and (not self._out or self._out[-1].endswith("\n")):
            self._out.append("> " * self._quote)
        self._out.append(text)

    def _line(self) -> None:
        self._open_gap(1)

    def _paragraph(self) -> None:
        self._open_gap(2)

    def _open_gap(self, lines: int) -> None:
        self._gap_quote = min(self._gap_quote, self._quote) if self._gap else self._quote
        self._gap = max(self._gap, lines)

    def _at_line_start(self) -> bool:
        return not self._out or self._gap > 0 or self._out[-1].endswith(("\n", "> "))

    def _tail(self) -> str:
        return "".join(self._out[-3:])
