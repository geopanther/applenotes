from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from applenotes.text import canonical, decode_note, encode_note, html_text, title


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("    a\r\n        b\r  c  ", "\ta\n\t\tb\n  c  "),
        ("\t    a\n      \n x    y ", "\t\ta\n\t  \n x    y "),
        ("   a", "   a"),
        ("", ""),
        ("\t x", "\t x"),
    ],
)
def test_indentation(source, expected):
    assert canonical(source) == expected
    assert canonical(expected) == expected


def test_configurable_indent():
    assert canonical("  x\n   y", 2) == "\tx\n\t y"
    with pytest.raises(ValueError):
        canonical("a", 0)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<div>A</div><div>B</div>", "A\nB"),
        ("<p>A</p><p>B</p>", "A\n\nB"),
        ("<div>A<br>B<br/>C<br />D</div>", "A\nB\nC\nD"),
        ("<div>A</div><div><br></div><div>B</div>", "A\n\nB"),
        ("<ul><li>A<ul><li>B</li></ul></li><li>C</li></ul>", "A\nB\nC"),
        ("<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>", "A\tB\nC\tD"),
        ('<a href="secret">Label</a> &amp; &lt; \u00a0\u00a0\u00a0\u00a0x', "Label & <     x"),
        ('<script>bad</script><style>bad</style><!--bad--><img alt="bad"><div>Good</div>', "Good"),
        ("<div>    indented</div><div>line  </div>", "\tindented\nline  "),
        ("<div>broken<b> bold", "broken bold"),
        ('<img src="x">', ""),
    ],
)
def test_html(html, expected):
    assert html_text(html) == expected


def part(content, subtype="plain", charset="utf-8"):
    m = EmailMessage()
    m.set_content(content, subtype=subtype, charset=charset)
    return m


def test_nested_alternatives_and_attachments():
    alt = part("Wrong")
    alt.add_alternative("<div>Title</div><div>    Body</div>", subtype="html")
    root = EmailMessage()
    root.make_mixed()
    root.attach(alt)
    root.add_attachment("Excluded text", filename="a.txt")
    root.add_attachment(b"\x00\x01", maintype="application", subtype="octet-stream", filename="b")
    note = decode_note(7, root.as_bytes())
    assert note.uid == 7
    assert note.text == "Title\n\tBody"
    assert note.apple_uuid is None


@pytest.mark.parametrize("charset", ["utf-8", "iso-8859-1"])
@pytest.mark.parametrize("cte", ["base64", "quoted-printable"])
def test_charsets(charset, cte):
    m = EmailMessage()
    m.set_content("café\n    x\n", charset=charset, cte=cte)
    assert decode_note(1, m.as_bytes()).text == "café\n\tx\n"


def test_malformed_and_attachment_only():
    assert decode_note(1, b"No headers\njust text").text == "No headers\njust text"
    assert decode_note(1, b"Content-Type: text/plain; charset=missing\n\nabc").text == "abc"
    msg = part("ignore")
    msg["Content-Disposition"] = 'attachment; filename="x.txt"'
    assert decode_note(1, msg.as_bytes()).text == ""


@pytest.mark.parametrize(
    "text", ["Title\n\tbody < &\n\n", "", "\nbody", "ü😀\n\t tab  ", "no newline"]
)
def test_upload_roundtrip(text):
    raw = encode_note(text, apple_uuid="fixed", message_id="<operation@applenotes.local>")
    m = BytesParser(policy=policy.default).parsebytes(raw)
    assert m.get_content_type() == "text/plain"
    assert not m.is_multipart()
    assert m["Subject"] == title(text)
    assert m["X-Uniform-Type-Identifier"] == "com.apple.mail-note"
    assert m["X-Universally-Unique-Identifier"] == "fixed"
    assert decode_note(1, raw).text == canonical(text)
    assert decode_note(1, raw).message_id == "<operation@applenotes.local>"


def test_generated_headers_are_fresh():
    a, b = [decode_note(1, encode_note("x")) for _ in range(2)]
    assert a.message_id != b.message_id
    assert a.apple_uuid != b.apple_uuid
    assert title("") == "Untitled"


def test_related_selects_root_and_ignores_embedded_email():
    root = EmailMessage()
    root.make_related()
    image = EmailMessage()
    image.set_content(b"binary", maintype="image", subtype="png")
    image["Content-ID"] = "<image>"
    body = part("<div>Body</div>", "html")
    body["Content-ID"] = "<body>"
    root.attach(image)
    root.attach(body)
    root.set_param("start", "<body>")
    assert decode_note(1, root.as_bytes()).text == "Body"
    outer = part("Main")
    outer.make_mixed()
    outer.add_attachment(part("Embedded"), subtype="rfc822")
    assert decode_note(1, outer.as_bytes()).text == "Main\n"
    empty = EmailMessage()
    empty.make_related()
    assert decode_note(1, empty.as_bytes()).text == ""


def test_unsupported_alternative_and_content_types():
    root = EmailMessage()
    root.make_alternative()
    nested = part("Plain")
    nested.make_mixed()
    root.attach(nested)
    assert decode_note(1, root.as_bytes()).text == "Plain\n"
    assert decode_note(1, b"Content-Type: application/pdf\n\nignore").text == ""
    m = EmailMessage()
    m.make_alternative()
    assert decode_note(1, m.as_bytes()).text == ""


def test_synthetic_mime_and_whitespace_fixtures():
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures"
    assert (
        decode_note(1, (fixtures / "apple-html.eml").read_bytes()).text
        == "Synthetic title\n\tIndented\n\nFinal & < text"
    )
    assert decode_note(1, (fixtures / "nested-mime.eml").read_bytes()).text == "Title\n\nBody"
    assert (
        canonical((fixtures / "whitespace.txt").read_bytes().decode())
        == "Title\n\tGroup\n\t  Remainder  \n\t\tMixed\n\t\nFinal"
    )


def test_empty_html_blocks_preserve_blank_lines():
    assert html_text("<div>A</div><div></div><div>B</div>") == "A\n\nB"
    assert html_text("<p>A</p><p></p><p>B</p>") == "A\n\n\nB"


def test_inline_embedded_message_is_not_note_body():
    outer = part("Main")
    outer.make_mixed()
    embedded = EmailMessage()
    embedded.set_type("message/rfc822")
    embedded.set_payload([part("Secret attached mail")])
    outer.attach(embedded)
    assert decode_note(1, outer.as_bytes()).text == "Main\n"
