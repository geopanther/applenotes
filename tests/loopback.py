"""Fragmented IMAP wire fixture backed by the independent mock server."""

import re
import socketserver
import threading
from contextlib import contextmanager

from tests.mock_server import MockServer


@contextmanager
def loopback(server: MockServer):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(3)
            client = server.client()
            self.wfile.write(b"* OK mock ready\r\n")
            while line := self.rfile.readline():
                tag, command, *rest = line.rstrip().split(b" ", 2)
                args = rest[0] if rest else b""
                command = command.upper()
                ok = True
                if command == b"CAPABILITY":
                    self.wfile.write(
                        b"* CAPABILITY IMAP4rev1"
                        + (b" UIDPLUS" if server.uidplus else b"")
                        + b"\r\n"
                    )
                elif command == b"LOGIN":
                    ok = b"wrong" not in args
                elif command == b"SELECT":
                    name = args.decode().strip('"')
                    try:
                        validity = client.select(name)
                        self.wfile.write(
                            (
                                f"* {len(client.inventory())} EXISTS\r\n"
                                f"* OK [UIDVALIDITY {validity}] valid\r\n"
                            ).encode()
                        )
                    except OSError:
                        ok = False
                elif command == b"CREATE":
                    client.select(args.decode().strip('"'), create=True)
                elif command == b"APPEND":
                    size = int(re.search(rb"\{(\d+)\}$", args)[1])
                    self.wfile.write(b"+ continue\r\n")
                    raw = self.rfile.read(size)
                    self.rfile.read(2)
                    uid = client.append(raw)
                    if uid:
                        self.wfile.write(
                            tag + f" OK [APPENDUID {server.validity} {uid}] appended\r\n".encode()
                        )
                        continue
                elif command == b"UID":
                    subcommand, *parts = args.split(b" ", 1)
                    tail = parts[0] if parts else b""
                    if subcommand == b"SEARCH":
                        rows = client.inventory()
                        if b"Message-ID" in tail:
                            wanted = tail.split(b"Message-ID", 1)[1].strip().strip(b'"').decode()
                            rows = {uid: rows[uid] for uid in client.find_message(wanted)}
                        self.wfile.write(
                            b"* SEARCH " + b" ".join(str(uid).encode() for uid in rows) + b"\r\n"
                        )
                    elif subcommand == b"FETCH":
                        uid = int(tail.split()[0])
                        raw = client.inventory().get(uid)
                        if raw is not None:
                            self.wfile.write(
                                f"* 1 FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode()
                            )
                            for offset in range(0, len(raw), 7):
                                self.wfile.write(raw[offset : offset + 7])
                            self.wfile.write(b")\r\n")
                    elif subcommand == b"STORE":
                        uid = int(tail.split()[0])
                        server.boxes[client.folder][uid][1].add("\\Deleted")
                    elif subcommand == b"EXPUNGE":
                        uid = int(tail)
                        server.boxes[client.folder].pop(uid, None)
                    else:
                        ok = False
                elif command == b"LOGOUT":
                    self.wfile.write(b"* BYE logout\r\n" + tag + b" OK logout\r\n")
                    return
                else:
                    ok = False
                self.wfile.write(tag + (b" OK done\r\n" if ok else b" NO refused\r\n"))

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as tcp:
        worker = threading.Thread(target=tcp.serve_forever, daemon=True)
        worker.start()
        try:
            yield tcp.server_address
        finally:
            tcp.shutdown()
            worker.join()
