"""MIME and HTML are reduced to plain text at the transport boundary."""

import re
from email import encoders, policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from uuid import uuid4

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from applenotes.models import RemoteNote


def canonical(text: str, width: int = 4) -> str:
    if width < 1:
        raise ValueError("indentation width must be positive")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    def indent(match: re.Match[str]) -> str:
        # Tabs already present are preserved; each contiguous run of leading spaces is converted.
        return re.sub(
            r" +", lambda m: "\t" * (len(m[0]) // width) + " " * (len(m[0]) % width), match[0]
        )

    return re.sub(r"^[ \t]+", indent, text, flags=re.MULTILINE)


def html_text(html: str, width: int = 4) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for item in soup(
        [
            "head",
            "script",
            "style",
            "img",
            "object",
            "embed",
            "iframe",
            "svg",
            "canvas",
            "audio",
            "video",
        ]
    ):
        item.decompose()
    chunks: list[str] = []

    def boundary(count: int = 1) -> None:
        if chunks:
            current = "".join(chunks)
            trailing = len(current) - len(current.rstrip("\n"))
            chunks.append("\n" * max(0, count - trailing))

    def render(node: object, pre: bool = False) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            value = str(node).replace("\xa0", " ")
            if not pre and value.isspace() and "\n" in value:
                return
            chunks.append(value)
        elif isinstance(node, Tag):
            name = node.name
            if name == "br":
                chunks.append("\n")
                return
            block = name in {
                "div",
                "p",
                "li",
                "ul",
                "ol",
                "tr",
                "blockquote",
                "pre",
                "section",
                "article",
                "h1",
                "h2",
                "h3",
                "h4",
                "hr",
            }
            count = 2 if name == "p" else 1
            if block:
                boundary(count)
            before = len(chunks)
            for child in node.children:
                render(child, pre or name == "pre")
            if name in {"div", "p", "li"} and len(chunks) == before:
                chunks.append("\n")
            if name in {"td", "th"} and node.find_next_sibling(["td", "th"]) is not None:
                chunks.append("\t")
            if block:
                boundary(count)

    render(soup)
    return canonical("".join(chunks).strip("\n"), width)


def _body(part: Message, width: int) -> str:
    if part.get_content_disposition() == "attachment" or part.get_filename():
        return ""
    if part.get_content_maintype() == "message":
        return ""
    if part.is_multipart():
        payload = part.get_payload()
        children = (
            [p for p in payload if isinstance(p, Message)] if isinstance(payload, list) else []
        )
        if part.get_content_subtype() == "alternative":
            eligible = [
                p
                for p in children
                if p.get_content_disposition() != "attachment" and not p.get_filename()
            ]
            for content_type in ("text/html", "text/plain"):
                selected = next((p for p in eligible if p.get_content_type() == content_type), None)
                if selected is not None:
                    return _body(selected, width)
            return _body(eligible[-1], width) if eligible else ""
        if part.get_content_subtype() == "related":
            start = part.get_param("start")
            selected = (
                next((p for p in children if p.get("Content-ID") == start), None) if start else None
            )
            return _body(selected or children[0], width) if children else ""
        return "\n".join(text for child in children if (text := _body(child, width)))
    if part.get_content_type() not in {"text/plain", "text/html"}:
        return ""
    raw = part.get_payload(decode=True)
    if not isinstance(raw, bytes):
        return ""
    try:
        text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return (
        html_text(text, width) if part.get_content_type() == "text/html" else canonical(text, width)
    )


def decode_note(uid: int, raw: bytes, width: int = 4) -> RemoteNote:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    return RemoteNote(
        uid=uid,
        text=_body(message, width),
        apple_uuid=str(message["X-Universally-Unique-Identifier"])
        if message["X-Universally-Unique-Identifier"]
        else None,
        message_id=str(message["Message-ID"]) if message["Message-ID"] else None,
    )


def title(text: str) -> str:
    first = text.split("\n", 1)[0].strip()
    return re.sub(r"[\x00-\x1f\x7f]", "", first) or "Untitled"


def encode_note(
    text: str, *, apple_uuid: str | None = None, message_id: str | None = None, width: int = 4
) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = title(text)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = message_id or make_msgid(domain="applenotes.local")
    message["X-Uniform-Type-Identifier"] = "com.apple.mail-note"
    message["X-Universally-Unique-Identifier"] = apple_uuid or str(uuid4()).upper()
    message["X-Mailer"] = "applenotes"
    message["MIME-Version"] = "1.0"
    message["Content-Type"] = 'text/plain; charset="utf-8"'
    message.set_payload(canonical(text, width).encode("utf-8"))
    encoders.encode_base64(message)
    return message.as_bytes()
