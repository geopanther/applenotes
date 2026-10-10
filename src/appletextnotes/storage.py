"""Constrained workspace IO, atomic state checkpoints, and an advisory lock."""

import fcntl
import os
import re
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from appletextnotes.models import State, safe_relative
from appletextnotes.text import title


class StorageError(Exception):
    pass


def filename(text: str, local_id: str) -> str:
    clean = re.sub(r"[^\w .-]", "_", title(text), flags=re.UNICODE).strip(" .") or "Untitled"
    while len(clean.encode("utf-8")) > 180:
        clean = clean[:-1]
    return f"{clean}--{local_id}.txt"


class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.meta = self.root / ".appletextnotes"
        self.state_path = self.meta / "state.json"
        self._check_meta()

    def _check_meta(self) -> None:
        if self.meta.is_symlink() or self.state_path.is_symlink():
            raise StorageError("metadata symlinks are not allowed")

    def path(self, relative: str) -> Path:
        try:
            safe_relative(relative)
        except ValueError as error:
            raise StorageError(str(error)) from None
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise StorageError("note symlinks and workspace escapes are not allowed")
        return path

    def read(self, relative: str) -> str | None:
        path = self.path(relative)
        try:
            return path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None

    def _atomic(self, target: Path, content: str) -> None:
        self._check_meta()
        self.meta.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="write-", dir=self.meta)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def write(self, relative: str, text: str) -> None:
        self._atomic(self.path(relative), text)

    def remove(self, relative: str) -> None:
        self.path(relative).unlink(missing_ok=True)

    def load(self) -> State:
        self._check_meta()
        try:
            return State.model_validate_json(self.state_path.read_bytes())
        except (OSError, ValidationError) as error:
            raise StorageError(
                "state is missing, malformed, or unsupported; it was not reset"
            ) from error

    def save(self, state: State) -> None:
        # Revalidate mutated nested models as well as data constructed by callers.
        checked = State.model_validate_json(state.model_dump_json())
        self._atomic(self.state_path, checked.model_dump_json(indent=2) + "\n")

    def initialize(self, state: State) -> None:
        if self.state_path.exists():
            raise StorageError("workspace is already initialized")
        self.save(state)

    @contextmanager
    def lock(self) -> Generator[None]:
        self._check_meta()
        self.meta.mkdir(parents=True, exist_ok=True)
        # Lock the stable metadata-directory inode. A removable lock file has an inode
        # race between waiting processes; directory flock also releases after a crash.
        directory = os.open(self.meta, os.O_RDONLY)
        try:
            try:
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StorageError("workspace is locked by another process") from None
            try:
                yield
            finally:
                fcntl.flock(directory, fcntl.LOCK_UN)
        finally:
            os.close(directory)

    def discover(self) -> list[str]:
        return sorted(
            path.name
            for path in self.root.glob("*.txt")
            if not path.name.endswith(".remote-conflict.txt")
        )
