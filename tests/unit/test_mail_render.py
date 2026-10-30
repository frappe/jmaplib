"""Bodies as text: the HTML converter, choosing a body, attachments, quoting."""

from __future__ import annotations

from typing import Any

from jmap.mail.render import RenderedBody, attachments_of, body_text, html_to_text, quote
from jmap.models.mail.objects import Email


def value(text: str, **flags: bool) -> dict[str, Any]:
    return {"value": text, **flags}


class TestHtmlToText:
    def test_blocks_and_inline_text(self):
        html = "<h1>Hi&nbsp;there</h1><p>Some   <b>bold</b>\ntext</p><div>next</div>"
        assert html_to_text(html) == "Hi there\n\nSome bold text\n\nnext"

    def test_hidden_elements(self):
        html = "<head><title>T</title><style>p{}</style></head><p>a<script>x()</script></p>"
        assert html_to_text(html) == "a"

    def test_links(self):
        html = (
            '<a href="https://x.com">site</a> <a href="https://y.com">https://y.com</a> '
            '<a href="mailto:a@b">mail</a> <a href="#top">top</a> <a>bare</a>'
        )
        assert html_to_text(html) == "site <https://x.com> https://y.com mail top bare"

    def test_lists(self):
        html = "<ul><li>one</li><li>two<ol><li>a</li><li>b</li></ol></li></ul><p>after</p>"
        assert html_to_text(html) == "- one\n- two\n  1. a\n  2. b\n\nafter"

    def test_quotes_keep_their_markers_across_paragraphs(self):
        html = (
            "<p>me</p><blockquote><p>you</p><p>more</p><blockquote>deep</blockquote></blockquote>x"
        )
        assert html_to_text(html) == "me\n\n> you\n>\n> more\n>\n> > deep\n\nx"

    def test_preformatted_text(self):
        assert html_to_text("<pre>  a\n    b</pre>c") == "  a\n    b\n\nc"

    def test_breaks_and_cells(self):
        assert html_to_text("a<br>b<br><br>c<table><tr><td>x</td><td>y</td></tr></table>") == (
            "a\nb\n\nc\n\n x y"
        )

    def test_whitespace_between_blocks_is_dropped(self):
        assert html_to_text("<p>a</p>\n  <p> </p><p>b</p>") == "a\n\nb"

    def test_stray_end_tags_are_harmless(self):
        assert html_to_text("</style></pre></blockquote></ul></a>text") == "text"

    def test_wrapping_keeps_quote_markers(self):
        html = "<p>" + "word " * 12 + "</p><blockquote>" + "q " * 30 + "</blockquote>"
        lines = html_to_text(html, width=30).split("\n")
        assert all(len(line) <= 30 for line in lines)
        assert lines[-1].startswith("> q")
        assert "" in lines


class TestBodyText:
    def email(self, **wire: Any) -> Email:
        return Email.from_wire(wire)

    def test_prefers_the_plain_part(self):
        email = self.email(
            textBody=[{"partId": "1", "type": "text/plain"}],
            htmlBody=[{"partId": "2", "type": "text/html"}],
            bodyValues={"1": value("plain\n"), "2": value("<p>html</p>")},
        )
        assert body_text(email) == RenderedBody("plain", "text")
        assert body_text(email, prefer="html") == RenderedBody("html", "html")

    def test_an_html_only_message(self):
        email = self.email(
            textBody=[{"partId": "2", "type": "text/html"}, {"partId": "3", "type": "text/plain"}],
            bodyValues={
                "2": value("<p>a</p>"),
                "3": value("b", isTruncated=True, isEncodingProblem=True),
            },
        )
        assert body_text(email) == RenderedBody("a\n\nb", "html", True, True)

    def test_falls_back_to_the_other_body_then_the_preview(self):
        only_html = self.email(
            textBody=[{"partId": "9"}],
            htmlBody=[{"partId": "2", "type": "text/html"}],
            bodyValues={"2": value("<i>x</i>"), "9": {"isTruncated": False}},
        )
        assert body_text(only_html).source == "html"
        assert body_text(self.email(preview="pre")) == RenderedBody("pre", "preview")
        assert body_text(self.email()) == RenderedBody("", "none")


class TestAttachments:
    def test_the_attachments_property(self):
        email = Email.from_wire({"attachments": [{"partId": "3", "name": "a.pdf"}]})
        assert [part.name for part in attachments_of(email)] == ["a.pdf"]

    def test_walking_the_structure(self):
        email = Email.from_wire(
            {
                "textBody": [{"partId": "1"}],
                "bodyStructure": {
                    "type": "multipart/mixed",
                    "subParts": [
                        {"partId": "1", "type": "text/plain"},
                        {"partId": "2", "type": "text/plain", "disposition": "attachment"},
                        {"partId": "3", "type": "image/png"},
                        {"partId": "4", "type": "text/calendar"},
                        {"partId": "5", "type": "multipart/alternative", "subParts": []},
                    ],
                },
            }
        )
        assert [part.part_id for part in attachments_of(email)] == ["2", "3"]
        assert attachments_of(Email.from_wire({})) == []


def test_quote():
    assert quote("a\n\nb") == "> a\n>\n> b"
    assert quote("x", prefix="| ") == "| x"
