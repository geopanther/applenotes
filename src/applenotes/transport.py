"""Injectable UID-only transport and a verified TLS imaplib implementation."""

import base64
import imaplib
import re
import ssl
from typing import Protocol

from applenotes.settings import Settings

IMAP_ERROR = imaplib.IMAP4.error


class TransportError(Exception):
    pass


class Transport(Protocol):
    def select(self, folder: str, *, create: bool = False) -> int: ...
    def inventory(self) -> dict[int, bytes]: ...
    def find_message(self, message_id: str) -> list[int]: ...
    def append(self, raw: bytes) -> int | None: ...
    def delete(self, uid: int) -> None: ...
    def close(self) -> None: ...


def _quote(value: str) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("invalid IMAP string")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mailbox_name(name: str) -> str:
    encoded: list[str] = []
    buffered: list[str] = []

    def flush() -> None:
        if buffered:
            value = (
                base64.b64encode("".join(buffered).encode("utf-16-be"))
                .decode()
                .rstrip("=")
                .replace("/", ",")
            )
            encoded.append("&" + value + "-")
            buffered.clear()

    for char in name:
        if " " <= char <= "~":
            flush()
            encoded.append("&-" if char == "&" else char)
        elif char in "\r\n\x00":
            raise ValueError("invalid mailbox name")
        else:
            buffered.append(char)
    flush()
    return _quote("".join(encoded))


class IMAPTransport:
    def __init__(self, wire: imaplib.IMAP4):
        self.wire = wire
        self.folder = ""
        self.validity: int | None = None

    @classmethod
    def connect(cls, settings: Settings) -> IMAPTransport:
        settings.require_network()
        assert settings.imap_server and settings.imap_username and settings.imap_password
        wire: imaplib.IMAP4 | None = None
        try:
            context = ssl.create_default_context()
            if settings.imap_security == "tls":
                wire = imaplib.IMAP4_SSL(
                    host=settings.imap_server,
                    port=settings.imap_port or 993,
                    ssl_context=context,
                    timeout=settings.timeout_seconds,
                )
            else:
                wire = imaplib.IMAP4(
                    host=settings.imap_server,
                    port=settings.imap_port or 143,
                    timeout=settings.timeout_seconds,
                )
                cls._check(wire.starttls(ssl_context=context), "STARTTLS")
            cls._check(
                wire.login(settings.imap_username, settings.imap_password.get_secret_value()),
                "login",
            )
            return cls(wire)
        except OSError, IMAP_ERROR, TransportError:
            if wire is not None:
                try:
                    wire.shutdown()
                except OSError:
                    pass
            raise TransportError("secure IMAP connection or authentication failed") from None

    @staticmethod
    def _check(response: tuple, operation: str) -> list:
        status, data = response
        if status != "OK":
            raise TransportError(f"IMAP {operation} failed")
        return data or []

    def _uid(self, command: str, *args: str | None) -> list:
        try:
            return self._check(
                self.wire.uid(command, *args),  # ty: ignore[invalid-argument-type]
                command,
            )
        except OSError, IMAP_ERROR:
            raise TransportError(f"IMAP {command} failed") from None

    def select(self, folder: str, *, create: bool = False) -> int:
        name = mailbox_name(folder)
        try:
            response = self.wire.select(name)
            if response[0] != "OK" and create:
                self._check(self.wire.create(name), "CREATE")
                response = self.wire.select(name)
            self._check(response, "SELECT (mailbox missing or unavailable)")
            _, values = self.wire.response("UIDVALIDITY")
            validity = int(values[0]) if values and values[0] else 0
            if validity <= 0:
                raise TransportError("mailbox has no valid UIDVALIDITY")
        except OSError, IMAP_ERROR, ValueError, TypeError:
            raise TransportError("IMAP mailbox selection failed") from None
        self.folder, self.validity = name, validity
        return validity

    def inventory(self) -> dict[int, bytes]:
        data = self._uid("SEARCH", None, "UNDELETED")
        try:
            uids = [int(uid) for row in data if isinstance(row, bytes) for uid in row.split()]
        except ValueError:
            raise TransportError("invalid SEARCH response") from None
        inventory: dict[int, bytes] = {}
        for uid in uids:
            rows = self._uid("FETCH", str(uid), "(UID BODY.PEEK[])")
            literal = next(
                (row for row in rows if isinstance(row, tuple) and isinstance(row[1], bytes)), None
            )
            if literal is None or not re.search(
                rb"\bUID " + str(uid).encode() + rb"\b", literal[0]
            ):
                raise TransportError("message changed while inventory was being fetched")
            inventory[uid] = literal[1]
        return inventory

    def find_message(self, message_id: str) -> list[int]:
        data = self._uid("SEARCH", None, "UNDELETED", "HEADER", "Message-ID", _quote(message_id))
        try:
            return [int(uid) for row in data if isinstance(row, bytes) for uid in row.split()]
        except ValueError:
            raise TransportError("invalid SEARCH response") from None

    def append(self, raw: bytes) -> int | None:
        try:
            data = self._check(self.wire.append(self.folder, None, None, raw), "APPEND")
            _, extra = self.wire.response("APPENDUID")
            blob = b" ".join(row for row in [*data, *(extra or [])] if isinstance(row, bytes))
            match = re.search(rb"(?:APPENDUID\s+|^)(\d+)\s+(\d+)(?:\]|$)", blob)
            if match:
                if int(match[1]) != self.validity:
                    raise TransportError("UIDVALIDITY changed during APPEND")
                return int(match[2])
            return None
        except OSError, IMAP_ERROR:
            raise TransportError("IMAP APPEND failed; outcome may be uncertain") from None

    def delete(self, uid: int) -> None:
        self._uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
        if "UIDPLUS" in self.wire.capabilities:
            self._uid("EXPUNGE", str(uid))

    def close(self) -> None:
        try:
            # Never CLOSE or mailbox-wide EXPUNGE: they can remove unrelated messages.
            self.wire.logout()
        except OSError, IMAP_ERROR:
            pass
