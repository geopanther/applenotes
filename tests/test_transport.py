import imaplib
from unittest.mock import MagicMock

import pytest

from applenotes.settings import Settings
from applenotes.text import encode_note
from applenotes.transport import IMAPTransport, TransportError, mailbox_name
from tests.loopback import loopback
from tests.mock_server import MockServer


@pytest.mark.parametrize("uidplus", [True, False])
def test_real_adapter_over_fragmented_loopback(uidplus):
    server = MockServer(uidplus=uidplus)
    raw = encode_note("Ü\n\ttabs", message_id="<wire@local>")
    server.add(raw)
    with loopback(server) as (host, port):
        wire = imaplib.IMAP4(host, port, timeout=3)
        wire.login("user", "secret")
        adapter = IMAPTransport(wire)
        assert adapter.select("Notes") == 10
        assert adapter.inventory() == {1: raw}
        assert adapter.find_message("<wire@local>") == [1]
        assert adapter.find_message("<absent@local>") == []
        assert adapter.append(raw) == (2 if uidplus else None)
        adapter.delete(1)
        assert list(adapter.inventory()) == [2]
        assert (1 in server.boxes["Notes"]) is (not uidplus)
        with pytest.raises(TransportError):
            adapter.select("Missing")
        assert adapter.select("New", create=True) == 10
        assert adapter.inventory() == {}
        adapter.close()


def test_mailbox_encoding():
    assert mailbox_name("Notes & More") == '"Notes &- More"'
    assert mailbox_name("日本語") == '"&ZeVnLIqe-"'
    with pytest.raises(ValueError):
        mailbox_name("bad\nname")


@pytest.mark.parametrize("security", ["tls", "starttls"])
def test_secure_connect(monkeypatch, security):
    wire = MagicMock()
    wire.login.return_value = ("OK", [b"ok"])
    wire.starttls.return_value = ("OK", [b"ok"])
    tls = MagicMock(return_value=wire)
    plain = MagicMock(return_value=wire)
    monkeypatch.setattr("applenotes.transport.imaplib.IMAP4_SSL", tls)
    monkeypatch.setattr("applenotes.transport.imaplib.IMAP4", plain)
    settings = Settings(
        imap_server="host",
        imap_username="user",
        imap_password="secret",
        imap_security=security,
        _env_file=None,
    )
    adapter = IMAPTransport.connect(settings)
    assert adapter.wire is wire
    assert wire.login.call_args.args == ("user", "secret")
    assert (tls.called, plain.called) == (security == "tls", security == "starttls")
    if security == "starttls":
        assert wire.starttls.called
        assert plain.call_args.kwargs["port"] == 143


@pytest.mark.parametrize("fault", ["connect", "starttls", "login"])
def test_connection_errors_are_redacted(monkeypatch, fault):
    wire = MagicMock()
    wire.login.return_value = ("OK", [])
    wire.starttls.return_value = ("OK", [])
    factory = MagicMock(return_value=wire)
    if fault == "connect":
        factory.side_effect = OSError("secret")
    else:
        getattr(wire, fault).side_effect = imaplib.IMAP4.error("secret")
    monkeypatch.setattr("applenotes.transport.imaplib.IMAP4", factory)
    settings = Settings(
        imap_server="host",
        imap_username="user",
        imap_password="secret",
        imap_security="starttls",
        _env_file=None,
    )
    with pytest.raises(TransportError) as error:
        IMAPTransport.connect(settings)
    assert "secret" not in str(error.value)


def test_protocol_bad_responses():
    wire = MagicMock()
    wire.capabilities = ()
    adapter = IMAPTransport(wire)
    for method, args in [
        ("inventory", ()),
        ("append", (b"x",)),
        ("delete", (1,)),
        ("find_message", ("<x>",)),
    ]:
        wire.uid.return_value = ("BAD", [b"private server details"])
        wire.append.return_value = ("NO", [b"private server details"])
        with pytest.raises(TransportError):
            getattr(adapter, method)(*args)


@pytest.mark.parametrize("response", [("OK", [b"bad"]), ("OK", [None])])
def test_invalid_search_data(response):
    wire = MagicMock()
    wire.uid.return_value = response
    adapter = IMAPTransport(wire)
    if response[1] == [None]:
        assert adapter.inventory() == {}
    else:
        with pytest.raises(TransportError):
            adapter.inventory()
        with pytest.raises(TransportError):
            adapter.find_message("<x>")


@pytest.mark.parametrize("rows", [[], [(b"1 FETCH (UID 999)", b"body")]])
def test_missing_or_wrong_fetch_literal(rows):
    wire = MagicMock()
    wire.uid.side_effect = [("OK", [b"1"]), ("OK", rows)]
    with pytest.raises(TransportError):
        IMAPTransport(wire).inventory()
    assert "BODY.PEEK[]" in wire.uid.call_args.args[-1]


def test_uid_disconnect_and_logout_failure():
    wire = MagicMock()
    wire.uid.side_effect = OSError("private")
    wire.logout.side_effect = OSError("private")
    adapter = IMAPTransport(wire)
    with pytest.raises(TransportError):
        adapter.inventory()
    adapter.close()
    wire.close.assert_not_called()
    wire.expunge.assert_not_called()


@pytest.mark.parametrize("value", [b"0", b"bad", None])
def test_invalid_uidvalidity(value):
    wire = MagicMock()
    wire.select.return_value = ("OK", [])
    wire.response.return_value = ("UIDVALIDITY", [value])
    with pytest.raises(TransportError):
        IMAPTransport(wire).select("Notes")


def test_append_epoch_mismatch_and_disconnect():
    wire = MagicMock()
    wire.append.return_value = ("OK", [b"[APPENDUID 99 3]"])
    wire.response.return_value = (None, [None])
    adapter = IMAPTransport(wire)
    adapter.validity = 10
    with pytest.raises(TransportError):
        adapter.append(b"x")
    wire.append.side_effect = OSError("private")
    with pytest.raises(TransportError):
        adapter.append(b"x")


def test_unsafe_search_string():
    with pytest.raises(ValueError):
        IMAPTransport(MagicMock()).find_message("bad\nstring")
