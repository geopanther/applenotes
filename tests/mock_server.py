"""Independent stateful server: no production imports or codec dependencies."""

from collections import defaultdict
from collections.abc import Callable
from email import policy
from email.parser import BytesParser


class MockServer:
    def __init__(self, *, uidplus=True):
        self.boxes = {"Notes": {}}
        self.validity = 10
        self.next_uid = 1
        self.uidplus = uidplus
        self.history = []
        self.failures = {}
        self.scheduled = defaultdict(list)
        self.counts = defaultdict(int)

    def client(self, username="user", password="secret"):
        if (username, password) != ("user", "secret"):
            raise OSError("authentication failed")
        return MockClient(self)

    def tick(self, command):
        self.history.append(command)
        self.counts[command] += 1
        for action in self.scheduled.pop((command, self.counts[command]), []):
            action()
        failure = self.failures.pop(command, None)
        if failure:
            raise OSError(failure)

    def schedule(self, command: str, occurrence: int, action: Callable):
        self.scheduled[command, occurrence].append(action)

    def add(self, raw: bytes, folder="Notes"):
        uid = self.next_uid
        self.next_uid += 1
        self.boxes[folder][uid] = [raw, set()]
        return uid

    def reset(self):
        self.validity += 1
        messages = [raw for raw, flags in self.boxes["Notes"].values() if "\\Deleted" not in flags]
        self.boxes["Notes"] = {}
        for raw in messages:
            self.add(raw)


class MockClient:
    def __init__(self, server):
        self.server = server
        self.folder = "Notes"

    def select(self, folder, *, create=False):
        self.server.tick("select")
        if folder not in self.server.boxes:
            if not create:
                raise OSError("mailbox missing")
            self.server.boxes[folder] = {}
        self.folder = folder
        return self.server.validity

    def inventory(self):
        self.server.tick("inventory")
        return {
            uid: raw
            for uid, (raw, flags) in self.server.boxes[self.folder].items()
            if "\\Deleted" not in flags
        }

    def find_message(self, message_id):
        self.server.tick("find")
        return [
            uid
            for uid, raw in self.inventory().items()
            if str(BytesParser(policy=policy.default).parsebytes(raw)["Message-ID"]) == message_id
        ]

    def append(self, raw):
        self.server.tick("append")
        uid = self.server.add(raw, self.folder)
        self.server.tick("append_response")
        return uid if self.server.uidplus else None

    def delete(self, uid):
        self.server.tick("delete")
        row = self.server.boxes[self.folder].get(uid)
        if row:
            row[1].add("\\Deleted")
            if self.server.uidplus:
                self.server.tick("expunge")
                del self.server.boxes[self.folder][uid]

    def close(self):
        self.server.tick("logout")
