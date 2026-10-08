"""Validated domain data and the single durable state format."""

import hashlib
from pathlib import PurePath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_relative(path: str) -> str:
    if (
        not path
        or PurePath(path).is_absolute()
        or len(PurePath(path).parts) != 1
        or path in {".", "..", ".applenotes"}
        or "/" in path
        or "\\" in path
        or "\x00" in path
    ):
        raise ValueError("note paths must be filenames within the workspace")
    return path


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Model):
    server: str
    username: str
    folder: str
    port: int
    security: Literal["tls", "starttls"]


class RemoteNote(Model):
    uid: int = Field(gt=0)
    text: str
    apple_uuid: str | None = None
    message_id: str | None = None


class Conflict(Model):
    kind: Literal["text", "deletion", "identity"]
    base: str
    local: str | None
    remote: str | None
    remote_uid: int | None = Field(default=None, gt=0)
    command: list[str] = Field(default_factory=list)
    candidates: list[RemoteNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_text_conflict(self) -> Self:
        if self.kind == "text" and (self.remote_uid is None or self.remote is None):
            raise ValueError("text conflicts require the observed remote version")
        return self


class NoteRecord(Model):
    local_id: str
    path: str
    base_text: str = ""
    base_hash: str = ""
    uid: int | None = Field(default=None, gt=0)
    apple_uuid: str | None = None
    message_id: str | None = None
    identity_evidence: str = "new"
    conflict: Conflict | None = None
    deleted: bool = False

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        safe_relative(self.path)
        if not self.local_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in self.local_id
        ):
            raise ValueError("invalid local ID")
        expected = digest(self.base_text)
        if self.base_hash and self.base_hash != expected:
            raise ValueError("base hash does not match base text")
        self.base_hash = expected
        return self

    @property
    def companion(self) -> str:
        return self.path.removesuffix(".txt") + ".remote-conflict.txt"

    def advance(self, text: str, remote: RemoteNote | None = None) -> None:
        self.base_text = text
        self.base_hash = digest(text)
        if remote is not None:
            self.uid = remote.uid
            self.apple_uuid = remote.apple_uuid
            self.message_id = remote.message_id


class PendingOperation(Model):
    operation_id: str
    note_id: str
    action: Literal["upload", "download", "delete_local", "delete_remote", "conflict"]
    phase: Literal["prepared", "appended", "verified", "retired"] = "prepared"
    text: str | None = None
    local_before: str | None = None
    remote_before: RemoteNote | None = None
    old_uid: int | None = Field(default=None, gt=0)
    new_uid: int | None = Field(default=None, gt=0)
    uidvalidity: int = Field(gt=0)
    message_id: str | None = None
    apple_uuid: str | None = None
    conflict: Conflict | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        if self.action == "upload" and (
            self.text is None or not self.message_id or not self.apple_uuid
        ):
            raise ValueError("uploads require text and operation-specific identities")
        if self.action in {"download", "conflict"} and self.text is None:
            raise ValueError("local replacements require text")
        if self.action == "conflict" and self.conflict is None:
            raise ValueError("conflict writes require original versions")
        if self.remote_before is not None and self.old_uid != self.remote_before.uid:
            raise ValueError("pending remote UID disagrees with its original version")
        if self.action != "upload" and self.phase != "prepared":
            raise ValueError("invalid checkpoint for operation")
        return self


class StructuredError(Model):
    code: str
    message: str


class State(Model):
    schema_version: Literal[1] = 1
    identity: Identity | None = None
    uidvalidity: int | None = Field(default=None, gt=0)
    notes: dict[str, NoteRecord] = Field(default_factory=dict)
    pending: dict[str, PendingOperation] = Field(default_factory=dict)
    last_successful_sync: str | None = None
    errors: list[StructuredError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mappings(self) -> Self:
        paths: set[str] = set()
        uids: set[int] = set()
        for key, note in self.notes.items():
            if key != note.local_id or note.path.casefold() in paths:
                raise ValueError("invalid note mapping or duplicate path")
            paths.add(note.path.casefold())
            if not note.deleted and note.uid is not None:
                if note.uid in uids:
                    raise ValueError("duplicate remote UID mapping")
                uids.add(note.uid)
        for key, operation in self.pending.items():
            if key != operation.operation_id or operation.note_id not in self.notes:
                raise ValueError("invalid pending operation mapping")
        return self


class MergeResult(Model):
    outcome: Literal["clean", "conflict"]
    text: str
    diagnostics: str = ""


class PlannedOperation(Model):
    note_id: str
    path: str
    action: str


class SyncResult(Model):
    operations: list[PlannedOperation] = Field(default_factory=list)
    conflicts: int = 0
    dry_run: bool = False
