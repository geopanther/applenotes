"""Reconcile versions and execute recoverable, individually checkpointed operations."""

import re
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

from appletextnotes.merge import MergeError, Merger
from appletextnotes.models import (
    Conflict,
    NoteRecord,
    PendingOperation,
    PlannedOperation,
    RemoteNote,
    State,
    StructuredError,
    SyncResult,
)
from appletextnotes.planner import decide
from appletextnotes.settings import Settings
from appletextnotes.storage import Storage, StorageError, filename
from appletextnotes.text import canonical, decode_note, encode_note
from appletextnotes.transport import Transport, TransportError


class SyncError(Exception):
    def __init__(self, message: str, *, invalid: bool = False):
        super().__init__(message)
        self.invalid = invalid


def has_markers(text: str) -> bool:
    return bool(re.search(r"^(?:<{7}|\|{7}|={7}|>{7})(?: |$)", text, re.MULTILINE))


class SyncEngine:
    def __init__(self, storage: Storage, transport: Transport, settings: Settings):
        self.store = storage
        self.transport = transport
        self.settings = settings

    def initialize(self, *, create_folder: bool = False) -> None:
        try:
            with self.store.lock():
                if self.store.state_path.exists():
                    raise SyncError("workspace is already initialized")
                validity = self.transport.select(self.settings.imap_folder, create=create_folder)
                self.store.initialize(
                    State(identity=self.settings.identity(), uidvalidity=validity)
                )
        except (OSError, TransportError, StorageError) as error:
            raise SyncError("initialization failed; check the workspace and mailbox") from error

    def _state(self) -> State:
        state = self.store.load()
        if state.identity != self.settings.identity():
            raise SyncError("workspace belongs to a different account or mailbox")
        return state

    def _inventory(self) -> dict[int, RemoteNote]:
        return {
            uid: decode_note(uid, raw, self.settings.indent_spaces)
            for uid, raw in self.transport.inventory().items()
        }

    def _local(self, note: NoteRecord) -> tuple[str | None, str | None]:
        raw = self.store.read(note.path)
        return raw, canonical(raw, self.settings.indent_spaces) if raw is not None else None

    def _match(
        self, state: State, inventory: dict[int, RemoteNote], validity: int
    ) -> tuple[dict[str, RemoteNote], set[int], set[str]]:
        matches: dict[str, RemoteNote] = {}
        available = set(inventory)
        active = [n for n in state.notes.values() if not n.deleted]
        # Do each identity tier globally, so weaker evidence cannot steal a known UID.
        for tier in ("uid", "apple_uuid", "message_id", "text"):
            proposed: dict[str, int] = {}
            for note in active:
                if note.local_id in matches:
                    continue
                candidates: list[int] = []
                if tier == "uid":
                    if state.uidvalidity == validity and note.uid in available:
                        candidates = [note.uid]  # type: ignore[list-item]
                else:
                    evidence = note.base_text if tier == "text" else getattr(note, tier)
                    if evidence is not None:
                        # Uniqueness is checked against the whole inventory, not just leftovers.
                        all_matches = [
                            uid
                            for uid, remote in inventory.items()
                            if getattr(remote, tier) == evidence
                        ]
                        if len(all_matches) == 1 and all_matches[0] in available:
                            candidates = all_matches
                if len(candidates) == 1:
                    proposed[note.local_id] = candidates[0]
            for note_id, uid in proposed.items():
                if list(proposed.values()).count(uid) == 1:
                    matches[note_id] = inventory[uid]
                    available.remove(uid)
                    state.notes[note_id].identity_evidence = tier
        missing = {n.local_id for n in active if n.local_id not in matches}
        ambiguous = missing if available and missing else set()
        return matches, available, ambiguous

    @staticmethod
    def _allowed(action: str, mode: str) -> bool:
        if mode == "pull" and action in {"upload", "merge", "delete_remote"}:
            return False
        return not (mode == "push" and action in {"download", "merge", "delete_local"})

    def sync(self, *, mode: str = "sync", dry_run: bool = False) -> SyncResult:
        if mode not in {"sync", "pull", "push"}:
            raise SyncError("unknown sync direction", invalid=True)
        try:
            with nullcontext() if dry_run else self.store.lock():
                return self._sync(mode=mode, dry_run=dry_run)
        except (OSError, StorageError) as error:
            raise SyncError("workspace is unavailable or locked") from error

    def _sync(self, *, mode: str, dry_run: bool) -> SyncResult:
        state: State | None = None
        result = SyncResult(dry_run=dry_run)
        try:
            state = self._state()
            validity = self.transport.select(self.settings.imap_folder)
            if state.pending:
                if dry_run:
                    result.operations.extend(
                        PlannedOperation(
                            note_id=p.note_id,
                            path=state.notes[p.note_id].path,
                            action="recover_" + p.action,
                        )
                        for p in state.pending.values()
                    )
                    return result
                for operation in list(state.pending.values()):
                    self._execute(state, operation)
                validity = self.transport.select(self.settings.imap_folder)
            inventory = self._inventory()
            matches, available, ambiguous = self._match(state, inventory, validity)
            # Preserve unmapped old UIDs in conflict evidence, never reuse them across epochs.
            for note in state.notes.values():
                if (
                    not note.deleted
                    and note.local_id not in matches
                    and state.uidvalidity != validity
                ):
                    note.uid = None
            state.uidvalidity = validity
            for note in list(state.notes.values()):
                if note.deleted:
                    continue
                raw, local = self._local(note)
                remote = matches.get(note.local_id)
                if note.local_id in ambiguous:
                    if not note.conflict:
                        note.conflict = Conflict(
                            kind="identity",
                            base=note.base_text,
                            local=local,
                            remote=None,
                            candidates=[inventory[uid] for uid in sorted(available)],
                        )
                    result.conflicts += 1
                    result.operations.append(
                        PlannedOperation(note_id=note.local_id, path=note.path, action="identity")
                    )
                    continue
                if note.conflict:
                    result.conflicts += 1
                    continue
                if remote is not None:
                    note.uid = remote.uid
                action = decide(note.base_text, local, remote.text if remote else None)
                result.operations.append(
                    PlannedOperation(note_id=note.local_id, path=note.path, action=action)
                )
                if action == "conflict":
                    result.conflicts += 1
                if dry_run or not self._allowed(action, mode):
                    continue
                if action in {"none", "advance"}:
                    if remote is not None:
                        note.advance(remote.text, remote)
                    continue
                if action == "tombstone":
                    note.deleted = True
                    continue
                text = local if action == "upload" else remote.text if remote else None
                conflict = None
                if action == "merge":
                    assert local is not None and remote is not None
                    merged = Merger(
                        self.settings.merge_command,
                        self.store.meta,
                        self.settings.merge_timeout_seconds,
                    ).merge(local, note.base_text, remote.text)
                    text = canonical(merged.text, self.settings.indent_spaces)
                    if merged.outcome == "conflict":
                        action = "conflict"
                        result.conflicts += 1
                        conflict = Conflict(
                            kind="text",
                            base=note.base_text,
                            local=local,
                            remote=remote.text,
                            remote_uid=remote.uid,
                            command=self._recorded_command(),
                        )
                    else:
                        action = "upload"
                elif action == "conflict":
                    conflict = Conflict(
                        kind="deletion",
                        base=note.base_text,
                        local=local,
                        remote=remote.text if remote else None,
                        remote_uid=remote.uid if remote else None,
                    )
                    text = self._deletion_markers(local, remote.text if remote else None)
                self._begin(state, note, action, text, raw, remote, conflict=conflict)
            if not ambiguous:
                for uid in sorted(available):
                    if mode == "push":
                        continue
                    remote = inventory[uid]
                    note = self._new_record(state, remote.text)
                    result.operations.append(
                        PlannedOperation(note_id=note.local_id, path=note.path, action="download")
                    )
                    if not dry_run:
                        self._begin(state, note, "download", remote.text, None, remote)
            if mode != "pull":
                known = {n.path for n in state.notes.values()}
                for path in self.store.discover():
                    if path in known:
                        continue
                    raw = self.store.read(path)
                    assert raw is not None
                    note = self._new_record(state, raw, path=path)
                    result.operations.append(
                        PlannedOperation(note_id=note.local_id, path=path, action="upload")
                    )
                    if not dry_run:
                        self._begin(
                            state,
                            note,
                            "upload",
                            canonical(raw, self.settings.indent_spaces),
                            raw,
                            None,
                        )
            if not dry_run:
                state.errors = []
                if result.conflicts == 0:
                    state.last_successful_sync = datetime.now(UTC).isoformat()
                self.store.save(state)
            return result
        except (
            OSError,
            UnicodeError,
            ValueError,
            TransportError,
            StorageError,
            MergeError,
            SyncError,
        ) as error:
            # Errors are deliberately generic: server/tool output may contain credentials.
            if state is not None and not dry_run:
                state.errors = [
                    StructuredError(
                        code="sync_failed",
                        message=(
                            "Synchronization stopped; original versions and recovery intent "
                            "are preserved."
                        ),
                    )
                ]
                try:
                    self.store.save(state)
                except OSError, ValueError, StorageError:
                    pass
            if isinstance(error, SyncError):
                raise
            raise SyncError("synchronization failed; inspect state.json and retry") from error

    def _recorded_command(self) -> list[str]:
        secret = self.settings.imap_password
        password = secret.get_secret_value() if secret is not None else ""
        return [
            arg.replace(password, "[redacted]") if password else arg
            for arg in self.settings.merge_command
        ]

    def _new_record(self, state: State, text: str, *, path: str | None = None) -> NoteRecord:
        local_id = uuid4().hex
        name = path or filename(text, local_id)
        if self.store.path(name).exists() and path is None:
            raise SyncError("generated note filename already exists")
        note = NoteRecord(local_id=local_id, path=name)
        state.notes[local_id] = note
        return note

    @staticmethod
    def _deletion_markers(local: str | None, remote: str | None) -> str:
        return (
            "<<<<<<< local\n"
            + (local if local is not None else "[deleted locally]")
            + "\n=======\n"
            + (remote if remote is not None else "[deleted remotely]")
            + "\n>>>>>>> remote\n"
        )

    def _begin(
        self,
        state: State,
        note: NoteRecord,
        action: str,
        text: str | None,
        local_before: str | None,
        remote: RemoteNote | None,
        *,
        conflict: Conflict | None = None,
    ) -> None:
        assert state.uidvalidity is not None
        operation_id = uuid4().hex
        if action not in {"upload", "download", "delete_local", "delete_remote", "conflict"}:
            raise SyncError("invalid operation")
        op = PendingOperation(
            operation_id=operation_id,
            note_id=note.local_id,
            action=action,
            text=text,
            local_before=local_before,
            remote_before=remote,
            old_uid=remote.uid if remote else None,
            uidvalidity=state.uidvalidity,
            message_id=f"<{operation_id}@appletextnotes.local>" if action == "upload" else None,
            apple_uuid=(remote.apple_uuid if remote else note.apple_uuid) or str(uuid4()).upper(),
            conflict=conflict,
        )
        state.pending[operation_id] = op
        self.store.save(state)
        self._execute(state, op)

    def _refresh(self, state: State, op: PendingOperation) -> dict[int, RemoteNote]:
        validity = self.transport.select(self.settings.imap_folder)
        inventory = self._inventory()
        if validity != op.uidvalidity:
            replacement = [
                r for r in inventory.values() if op.message_id and r.message_id == op.message_id
            ]
            if len(replacement) > 1:
                raise SyncError("pending upload has ambiguous identity after UIDVALIDITY reset")
            previous = op.remote_before
            recovered = None
            if previous is not None:
                for attribute, evidence in (
                    ("message_id", previous.message_id),
                    ("apple_uuid", previous.apple_uuid),
                    ("text", previous.text),
                ):
                    candidates = [
                        r
                        for r in inventory.values()
                        if evidence is not None
                        and getattr(r, attribute) == evidence
                        and r not in replacement
                    ]
                    if len(candidates) == 1 and candidates[0].text == previous.text:
                        recovered = candidates[0]
                        break
                if recovered is None and any(r not in replacement for r in inventory.values()):
                    raise SyncError(
                        "pending original has uncertain identity after UIDVALIDITY reset"
                    )
            matches, _, _ = self._match(state, inventory, validity)
            for note in state.notes.values():
                if not note.deleted:
                    matched = matches.get(note.local_id)
                    note.uid = matched.uid if matched else None
            op.remote_before = recovered
            op.old_uid = recovered.uid if recovered else None
            op.new_uid = replacement[0].uid if replacement else None
            op.uidvalidity = validity
            # Leave the state's epoch unchanged until the ordinary reconciliation pass;
            # no old UID is used to identify another note during this recovery.
            self.store.save(state)
        return inventory

    @staticmethod
    def _check_absent(state: State, note: NoteRecord, inventory: dict[int, RemoteNote]) -> None:
        known = {n.uid for n in state.notes.values() if not n.deleted}
        if any(r.uid not in known or r.uid == note.uid for r in inventory.values()):
            raise SyncError("a remote version appeared while its deletion was being reconciled")

    def _check_local(
        self, note: NoteRecord, op: PendingOperation, *, allow_result: bool = False
    ) -> str | None:
        current = self.store.read(note.path)
        if current != op.local_before and not (allow_result and current == op.text):
            raise SyncError(
                "local file changed during synchronization; pending versions were preserved"
            )
        return current

    @staticmethod
    def _check_remote(
        op: PendingOperation, inventory: dict[int, RemoteNote], *, allow_absent: bool = False
    ) -> None:
        if op.remote_before is None:
            return
        # A concurrently appended replacement can coexist with or retire the old UID.
        if op.remote_before.apple_uuid and any(
            r.uid not in {op.old_uid, op.new_uid} and r.apple_uuid == op.remote_before.apple_uuid
            for r in inventory.values()
        ):
            raise SyncError("multiple remote versions appeared during synchronization")
        current = inventory.get(op.old_uid)
        if current is None and allow_absent:
            return
        if current != op.remote_before:
            raise SyncError("remote version changed during synchronization; inspect pending state")

    def _execute(self, state: State, op: PendingOperation) -> None:
        note = state.notes[op.note_id]
        inventory = self._refresh(state, op)
        if op.action == "upload":
            assert op.message_id is not None and op.text is not None
            if op.phase == "prepared":
                existing = self.transport.find_message(op.message_id)
                if len(existing) > 1:
                    raise SyncError("multiple messages match a pending upload")
                if existing:
                    op.new_uid = existing[0]
                else:
                    self._check_local(note, op)
                    self._check_remote(op, inventory)
                    op.new_uid = self.transport.append(
                        encode_note(
                            op.text,
                            apple_uuid=op.apple_uuid,
                            message_id=op.message_id,
                            width=self.settings.indent_spaces,
                        )
                    )
                op.phase = "appended"
                self.store.save(state)
            if op.new_uid is None:
                found = self.transport.find_message(op.message_id)
                if len(found) != 1:
                    raise SyncError("cannot uniquely verify pending upload")
                op.new_uid = found[0]
                self.store.save(state)
            inventory = self._refresh(state, op)
            replacement = inventory.get(op.new_uid)
            if (
                replacement is None
                or replacement.message_id != op.message_id
                or replacement.text != op.text
                or replacement.apple_uuid != op.apple_uuid
            ):
                raise SyncError("uploaded message did not verify; original note retained")
            if op.phase == "appended":
                op.phase = "verified"
                self.store.save(state)
            if op.phase == "verified":
                self._check_remote(op, inventory, allow_absent=True)
                if op.old_uid is not None:
                    # Retry scoped retirement even if SEARCH hides a previously flagged UID.
                    self.transport.delete(op.old_uid)
                op.phase = "retired"
                self.store.save(state)
            # A local edit after APPEND must survive. The verified upload becomes the base;
            # the next pass sees the new local edit and uploads it independently.
            current = self.store.read(note.path)
            if current == op.local_before:
                self.store.write(note.path, op.text)
            note.advance(op.text, replacement)
            note.deleted = False
            if note.conflict:
                self.store.remove(note.companion)
                note.conflict = None
        elif op.action in {"download", "delete_local", "conflict"}:
            if op.remote_before is None:
                self._check_absent(state, note, inventory)
            self._check_remote(op, inventory)
            if op.action == "delete_local":
                current = self.store.read(note.path)
                if current is not None:
                    self._check_local(note, op)
                    self.store.remove(note.path)
                note.deleted = True
            else:
                self._check_local(note, op, allow_result=True)
                assert op.text is not None
                self.store.write(note.path, op.text)
                if op.action == "conflict":
                    self.store.write(note.companion, op.text)
                    note.conflict = op.conflict
                else:
                    note.advance(op.text, op.remote_before)
        elif op.action == "delete_remote":
            self._check_local(note, op)
            self._check_remote(op, inventory, allow_absent=True)
            if op.old_uid is not None:
                self.transport.delete(op.old_uid)
            note.deleted = True
        del state.pending[op.operation_id]
        self.store.save(state)

    def link(self, path: str, remote_uid: int) -> None:
        try:
            with self.store.lock():
                state = self._state()
                if state.pending:
                    raise SyncError("recover pending operations before linking", invalid=True)
                validity = self.transport.select(self.settings.imap_folder)
                inventory = self._inventory()
                remote = inventory.get(remote_uid)
                note = next(
                    (n for n in state.notes.values() if n.path == path and not n.deleted), None
                )
                self.store.path(path)
                if remote is None or note is None:
                    raise SyncError(
                        "link requires a tracked file and an existing remote UID", invalid=True
                    )
                if any(
                    n.local_id != note.local_id and n.uid == remote_uid and not n.deleted
                    for n in state.notes.values()
                ):
                    raise SyncError("remote UID is already linked", invalid=True)
                if state.uidvalidity != validity:
                    raise SyncError(
                        "run sync to inventory the new UIDVALIDITY before linking", invalid=True
                    )
                note.uid, note.apple_uuid, note.message_id = (
                    remote.uid,
                    remote.apple_uuid,
                    remote.message_id,
                )
                note.identity_evidence = "explicit_link"
                if note.conflict is not None and note.conflict.kind == "identity":
                    note.conflict = None
                self.store.save(state)
        except (OSError, TransportError, StorageError, ValueError) as error:
            raise SyncError("link failed", invalid=True) from error

    def resolve(self, path: str) -> None:
        try:
            with self.store.lock():
                state = self._state()
                if state.pending:
                    raise SyncError("recover pending operations before resolving", invalid=True)
                note = next(
                    (n for n in state.notes.values() if n.path == path and not n.deleted), None
                )
                if note is None or note.conflict is None:
                    raise SyncError("file has no recorded conflict", invalid=True)
                conflict = note.conflict
                if conflict.kind == "identity":
                    raise SyncError("use link to resolve uncertain identity", invalid=True)
                raw, local = self._local(note)
                if local is None or has_markers(local):
                    raise SyncError(
                        "remove conflict markers and save the file before resolving", invalid=True
                    )
                validity = self.transport.select(self.settings.imap_folder)
                inventory = self._inventory()
                matches, available, ambiguous = self._match(state, inventory, validity)
                if note.local_id in ambiguous:
                    raise SyncError("remote identity changed; use link before resolving")
                remote = matches.get(note.local_id)
                state.uidvalidity = validity
                expected = conflict.remote
                if remote is not None and remote.text != expected:
                    merged = Merger(
                        self.settings.merge_command,
                        self.store.meta,
                        self.settings.merge_timeout_seconds,
                    ).merge(local, expected or "", remote.text)
                    if merged.outcome == "conflict":
                        newer = Conflict(
                            kind="text",
                            base=expected or "",
                            local=local,
                            remote=remote.text,
                            remote_uid=remote.uid,
                            command=self._recorded_command(),
                        )
                        self._begin(
                            state,
                            note,
                            "conflict",
                            canonical(merged.text, self.settings.indent_spaces),
                            raw,
                            remote,
                            conflict=newer,
                        )
                        return
                    local = canonical(merged.text, self.settings.indent_spaces)
                self._begin(state, note, "upload", local, raw, remote)
        except (OSError, TransportError, StorageError, ValueError, MergeError) as error:
            raise SyncError("conflict resolution failed; original versions retained") from error
