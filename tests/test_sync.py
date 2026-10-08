import pytest

from applenotes.engine import SyncEngine, SyncError
from applenotes.settings import Settings
from applenotes.storage import Storage
from applenotes.text import decode_note, encode_note
from tests.mock_server import MockServer


@pytest.fixture
def setup(tmp_path):
    server = MockServer()
    settings = Settings(
        imap_server="test", imap_username="user", imap_password="secret", _env_file=None
    )
    store = Storage(tmp_path)
    engine = SyncEngine(store, server.client(), settings)
    engine.initialize()
    return server, store, engine


def add(server, text, **kwargs):
    return server.add(encode_note(text, **kwargs))


def tracked(store):
    records = [n for n in store.load().notes.values() if not n.deleted]
    assert len(records) == 1
    return records[0]


def replace(server, uid, text, *, preserve=True):
    old = decode_note(uid, server.boxes["Notes"][uid][0])
    del server.boxes["Notes"][uid]
    return add(server, text, apple_uuid=old.apple_uuid if preserve else None)


def test_import_and_plaintext_upload(setup):
    server, store, engine = setup
    uid = server.add(
        b"Content-Type: text/html\nX-Universally-Unique-Identifier: uuid\n\n"
        b"<div>Title</div><div>    Body</div>"
    )
    engine.sync()
    record = tracked(store)
    assert store.read(record.path) == "Title\n\tBody"
    assert record.uid == uid
    assert "append" not in server.history
    store.write(record.path, "New title\n    Body\n")
    engine.sync()
    record = tracked(store)
    assert record.path.startswith("Title--")
    assert record.uid != uid
    remote = server.client().inventory()
    assert len(remote) == 1
    assert decode_note(record.uid, remote[record.uid]).text == "New title\n\tBody\n"
    assert b"text/plain" in remote[record.uid] and b"text/html" not in remote[record.uid]
    before = server.counts["append"]
    engine.sync()
    assert server.counts["append"] == before


def test_new_local_and_multiple_notes(setup):
    server, store, engine = setup
    store.write("mine.txt", "Local\nbody")
    add(server, "Remote")
    engine.sync()
    assert len(store.load().notes) == 2
    assert len(server.client().inventory()) == 2
    assert store.read("mine.txt") == "Local\nbody"


def test_remote_edit_identity_reset_and_identical_edits(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase\n")
    engine.sync()
    record = tracked(store)
    new_uid = replace(server, uid, "Title\nremote\n")
    engine.sync()
    assert tracked(store).uid == new_uid
    assert store.read(record.path) == "Title\nremote\n"
    server.reset()
    store.write(record.path, "Title\nlocal\n")
    engine.sync()
    assert tracked(store).base_text == "Title\nlocal\n"
    replace(server, tracked(store).uid, "Title\nsame\n")
    store.write(record.path, "Title\nsame\n")
    before = server.counts["append"]
    engine.sync()
    assert tracked(store).base_text == "Title\nsame\n"
    assert server.counts["append"] == before


def test_disjoint_merge(setup):
    server, store, engine = setup
    uid = add(server, "Title\na\nb\nc\n")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nA\nb\nc\n")
    replace(server, uid, "Title\na\nb\nC\n")
    engine.sync()
    assert store.read(record.path) == "Title\nA\nb\nC\n"
    assert tracked(store).base_text == store.read(record.path)


def test_conflict_copies_repeated_sync_and_resolve(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase\n")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal\n")
    remote_uid = replace(server, uid, "Title\nremote\n")
    result = engine.sync()
    assert result.conflicts == 1
    record = tracked(store)
    assert record.conflict.local == "Title\nlocal\n"
    assert record.conflict.base == "Title\nbase\n"
    assert store.read(record.path) == store.read(record.companion)
    assert list(server.client().inventory()) == [remote_uid]
    with pytest.raises(SyncError):
        engine.resolve(record.path)
    store.write(record.path, "Title\nresolved\n")
    engine.sync()
    assert store.read(record.path) == "Title\nresolved\n"
    assert len(store.load().notes) == 1
    engine.resolve(record.path)
    assert tracked(store).conflict is None
    assert not store.path(record.companion).exists()
    assert tracked(store).base_text == "Title\nresolved\n"


@pytest.mark.parametrize("deleted_side", ["local", "remote", "both"])
def test_deletion_propagation(setup, deleted_side):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    if deleted_side in ("local", "both"):
        store.path(record.path).unlink()
    if deleted_side in ("remote", "both"):
        del server.boxes["Notes"][uid]
    engine.sync()
    assert store.load().notes[record.local_id].deleted
    assert store.read(record.path) is None
    assert server.client().inventory() == {}
    engine.sync()
    assert len(store.load().notes) == 1


@pytest.mark.parametrize("deleted_side", ["local", "remote"])
def test_edit_delete_preserves_content(setup, deleted_side):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    if deleted_side == "local":
        store.path(record.path).unlink()
        replace(server, uid, "Title\nremote")
    else:
        del server.boxes["Notes"][uid]
        store.write(record.path, "Title\nlocal")
    result = engine.sync()
    assert result.conflicts == 1
    assert tracked(store).conflict.kind == "deletion"
    assert store.read(record.path) is not None
    engine.sync()
    assert not tracked(store).deleted


def test_identity_ambiguity_and_link(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    new_uid = replace(server, uid, "Title\nchanged", preserve=False)
    result = engine.sync()
    assert result.conflicts >= 1
    assert store.read(record.path) == "Title\nbase"
    assert len(store.load().notes) == 1
    engine.link(record.path, new_uid)
    engine.sync()
    assert store.read(record.path) == "Title\nchanged"


def test_exact_content_recovery_and_duplicate_identity(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    replace(server, uid, record.base_text, preserve=False)
    server.reset()
    engine.sync()
    assert len(store.load().notes) == 1
    assert tracked(store).conflict is None


@pytest.mark.parametrize("mode", ["sync", "pull", "push"])
def test_dry_run_immutability(setup, mode):
    server, store, engine = setup
    add(server, "Remote")
    store.write("new.txt", "New")
    before = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    remote = server.client().inventory()
    engine.sync(mode=mode, dry_run=True)
    assert {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before
    assert server.client().inventory() == remote


def test_direction_filters(setup):
    server, store, engine = setup
    add(server, "Remote")
    store.write("new.txt", "New")
    engine.sync(mode="pull")
    assert server.counts["append"] == 0
    assert len(store.load().notes) == 1
    engine.sync(mode="push")
    assert server.counts["append"] == 1
    assert len(store.load().notes) == 2


@pytest.mark.parametrize("failure", ["append", "append_response", "delete", "expunge"])
def test_restart_recovery(setup, failure):
    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    server.failures[failure] = "injected disconnect"
    with pytest.raises(SyncError):
        engine.sync()
    assert store.load().pending
    recovered = SyncEngine(store, server.client(), engine.settings)
    recovered.sync()
    assert not store.load().pending
    assert tracked(store).base_text == "Title\nedit"
    assert len(server.client().inventory()) == 1
    assert server.counts["append_response"] == 1


def test_unrelated_deleted_messages_preserved(setup):
    server, store, engine = setup
    uid = add(server, "Title")
    unrelated = add(server, "Other")
    server.boxes["Notes"][unrelated][1].add("\\Deleted")
    engine.sync()
    store.path(tracked(store).path).unlink()
    engine.sync()
    assert uid not in server.boxes["Notes"]
    assert unrelated in server.boxes["Notes"]


def test_missing_mailbox_and_account_mismatch(setup):
    server, store, engine = setup
    del server.boxes["Notes"]
    with pytest.raises(SyncError):
        engine.sync()
    assert "Notes" not in server.boxes
    other = engine.settings.model_copy(update={"imap_username": "other"})
    with pytest.raises(SyncError):
        SyncEngine(store, server.client(), other).sync()


def test_initialization_create_and_repeat(tmp_path):
    server = MockServer()
    del server.boxes["Notes"]
    settings = Settings(
        imap_server="test", imap_username="user", imap_password="secret", _env_file=None
    )
    store = Storage(tmp_path)
    engine = SyncEngine(store, server.client(), settings)
    with pytest.raises(SyncError):
        engine.initialize()
    engine.initialize(create_folder=True)
    with pytest.raises(SyncError):
        engine.initialize()
    assert store.load().identity == settings.identity()


def test_local_edit_during_remote_refresh(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    replace(server, uid, "Title\nremote")
    server.schedule(
        "inventory", server.counts["inventory"] + 2, lambda: store.write(record.path, "Title\nrace")
    )
    with pytest.raises(SyncError):
        engine.sync()
    assert store.read(record.path) == "Title\nrace"


def test_remote_edit_during_upload(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal")
    server.schedule(
        "inventory", server.counts["inventory"] + 2, lambda: replace(server, uid, "Title\nrace")
    )
    with pytest.raises(SyncError):
        engine.sync()
    assert store.read(record.path) == "Title\nlocal"
    assert server.counts["append"] == 0


def test_merge_failure_preserves_versions(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal")
    replace(server, uid, "Title\nremote")
    engine.settings.merge_command = ["missing-program", "{local}", "{base}", "{remote}"]
    with pytest.raises(SyncError):
        engine.sync()
    assert store.read(record.path) == "Title\nlocal"
    assert tracked(store).base_text == "Title\nbase"
    assert server.counts["append"] == 0


def test_message_id_identity_when_uuid_changes(setup):
    server, store, engine = setup
    uid = add(server, "Original", message_id="<stable@example>")
    engine.sync()
    record = tracked(store)
    del server.boxes["Notes"][uid]
    new = add(server, "Edited", message_id="<stable@example>")
    engine.sync()
    assert tracked(store).uid == new
    assert tracked(store).identity_evidence == "message_id"
    assert store.read(record.path) == "Edited"


def test_duplicate_uuid_requires_unique_evidence(setup):
    server, store, engine = setup
    uid = add(server, "Original", apple_uuid="duplicate")
    engine.sync()
    del server.boxes["Notes"][uid]
    add(server, "One", apple_uuid="duplicate")
    add(server, "Two", apple_uuid="duplicate")
    assert engine.sync().conflicts == 1
    assert len(store.load().notes) == 1


def test_reset_with_colliding_old_uids_preserves_identity(setup):
    server, store, engine = setup
    one = add(server, "One", apple_uuid="one")
    two = add(server, "Two", apple_uuid="two")
    engine.sync()
    a, b = server.boxes["Notes"][one], server.boxes["Notes"][two]
    server.boxes["Notes"] = {one: b, two: a}
    server.validity += 1
    engine.sync()
    assert {n.base_text: n.uid for n in store.load().notes.values()} == {"One": two, "Two": one}


@pytest.mark.parametrize("uidplus", [True, False])
def test_append_fallback_and_lost_response(setup, uidplus):
    server, store, engine = setup
    server.uidplus = uidplus
    store.write("local.txt", "New\n\ttab")
    server.failures["append_response"] = "lost"
    with pytest.raises(SyncError):
        engine.sync()
    dry_before = store.state_path.read_bytes()
    preview = engine.sync(dry_run=True)
    assert preview.operations[0].action == "recover_upload"
    assert store.state_path.read_bytes() == dry_before
    engine.sync()
    assert server.counts["append"] == 1
    assert len(server.client().inventory()) == 1


@pytest.mark.parametrize("checkpoint", ["prepared", "appended", "verified", "retired", "completed"])
def test_state_save_failure_at_each_upload_checkpoint(setup, monkeypatch, checkpoint):
    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    original = store.save
    failed = False

    def save(state):
        nonlocal failed
        pending = list(state.pending.values())
        stage = pending[0].phase if pending else "completed"
        if not failed and stage == checkpoint:
            failed = True
            raise OSError("state disk failure")
        original(state)

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(SyncError):
        engine.sync()
    assert failed
    engine.sync()
    assert tracked(store).base_text == "Title\nedit"
    assert not store.load().pending
    assert len(server.client().inventory()) == 1
    assert server.counts["append_response"] == 1


def test_download_restart_after_local_replacement(setup, monkeypatch):
    server, store, engine = setup
    add(server, "Remote")
    original = store.save
    failed = False

    def save(state):
        nonlocal failed
        if not failed and not state.pending and state.notes:
            failed = True
            raise OSError("checkpoint failure")
        original(state)

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(SyncError):
        engine.sync()
    engine.sync()
    assert len(store.load().notes) == 1
    assert store.read(tracked(store).path) == "Remote"


def test_local_edit_after_append_survives(setup):
    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    server.schedule("append_response", 1, lambda: store.write(record.path, "Title\nlater"))
    engine.sync()
    assert store.read(record.path) == "Title\nlater"
    engine.sync()
    assert tracked(store).base_text == "Title\nlater"


def test_remote_disappearance_after_append_preserves_concurrent_version(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal")
    server.schedule("append_response", 1, lambda: replace(server, uid, "Title\nrace"))
    with pytest.raises(SyncError):
        engine.sync()
    assert len(server.client().inventory()) == 2
    assert store.load().pending
    assert store.read(record.path) == "Title\nlocal"


def test_remote_reappears_before_local_delete(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase", apple_uuid="stable")
    engine.sync()
    record = tracked(store)
    del server.boxes["Notes"][uid]
    server.schedule(
        "inventory",
        server.counts["inventory"] + 2,
        lambda: add(server, "Title\nrace", apple_uuid="stable"),
    )
    with pytest.raises(SyncError):
        engine.sync()
    assert store.read(record.path) == "Title\nbase"


def test_pending_upload_survives_uidvalidity_reset(setup):
    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    server.failures["append_response"] = "lost"
    with pytest.raises(SyncError):
        engine.sync()
    server.reset()
    engine.sync()
    assert not store.load().pending
    assert len(server.client().inventory()) == 1
    assert tracked(store).base_text == "Title\nedit"
    assert server.counts["append"] == 1


@pytest.mark.parametrize("clean", [True, False])
def test_resolve_rechecks_new_remote_edits(setup, clean):
    server, store, engine = setup
    uid = add(server, "Title\nbase\nseparator\ntail\n")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal\nseparator\ntail\n")
    remote_uid = replace(server, uid, "Title\nremote\nseparator\ntail\n")
    engine.sync()
    store.write(record.path, "Title\nresolved\nseparator\ntail\n")
    text = "Title\nremote\nseparator\nTAIL\n" if clean else "Title\nnew remote\nseparator\ntail\n"
    replace(server, remote_uid, text)
    engine.resolve(record.path)
    assert (tracked(store).conflict is None) is clean
    if clean:
        assert store.read(record.path) == "Title\nresolved\nseparator\nTAIL\n"
    else:
        assert "<<<<<<<" in store.read(record.path)
        assert tracked(store).conflict.remote == text


@pytest.mark.parametrize("deleted_side", ["local", "remote"])
def test_resolve_deletion_conflict(setup, deleted_side):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    if deleted_side == "local":
        store.remove(record.path)
        replace(server, uid, "Title\nremote")
    else:
        del server.boxes["Notes"][uid]
        store.write(record.path, "Title\nlocal")
    engine.sync()
    store.write(record.path, "Title\nkeep")
    engine.resolve(record.path)
    assert tracked(store).conflict is None
    assert tracked(store).base_text == "Title\nkeep"
    assert len(server.client().inventory()) == 1


def test_duplicate_operation_message_id_does_not_retry(setup):
    server, store, engine = setup
    store.write("local.txt", "Local")
    server.failures["append_response"] = "lost"
    with pytest.raises(SyncError):
        engine.sync()
    raw = next(iter(server.client().inventory().values()))
    server.add(raw)
    with pytest.raises(SyncError):
        engine.sync()
    assert server.counts["append"] == 1


def test_upload_verification_failure_retains_old_uid(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")

    def corrupt():
        new_uid = max(server.boxes["Notes"])
        server.boxes["Notes"][new_uid][0] = encode_note("Corrupted")

    server.schedule("append_response", 1, corrupt)
    with pytest.raises(SyncError):
        engine.sync()
    assert uid in server.boxes["Notes"]
    assert tracked(store).base_text == "Title\nbase"


def test_invalid_commands_and_link_guards(setup):
    server, store, engine = setup
    a = add(server, "A")
    b = add(server, "B")
    engine.sync()
    records = {n.base_text: n for n in store.load().notes.values()}
    with pytest.raises(SyncError):
        engine.sync(mode="invalid")
    with pytest.raises(SyncError):
        engine.link(records["A"].path, b)
    with pytest.raises(SyncError):
        engine.resolve("missing.txt")
    server.reset()
    with pytest.raises(SyncError):
        engine.link(records["A"].path, max(server.boxes["Notes"]))
    assert a != b


@pytest.mark.parametrize("checkpoint", ["prepared", "appended", "verified", "retired", "completed"])
def test_process_crash_at_upload_checkpoint(setup, monkeypatch, checkpoint):
    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    original = store.save

    def crash(state):
        pending = list(state.pending.values())
        stage = pending[0].phase if pending else "completed"
        if stage == checkpoint:
            raise KeyboardInterrupt("simulate process termination")
        original(state)

    monkeypatch.setattr(store, "save", crash)
    with pytest.raises(KeyboardInterrupt):
        engine.sync()
    monkeypatch.setattr(store, "save", original)
    SyncEngine(store, server.client(), engine.settings).sync()
    assert not store.load().pending
    assert tracked(store).base_text == "Title\nedit"
    assert len(server.client().inventory()) == 1
    assert server.counts["append"] == 1


def test_duplicate_local_bases_do_not_steal_recovery_identity(setup):
    server, store, engine = setup
    first = add(server, "Same")
    second = add(server, "Same")
    engine.sync()
    del server.boxes["Notes"][first]
    del server.boxes["Notes"][second]
    add(server, "Same")
    assert engine.sync().conflicts == 2
    assert len(store.load().notes) == 2
    assert all(n.conflict.candidates for n in store.load().notes.values())


def test_pending_upload_local_edit_after_lost_response(setup):
    server, store, engine = setup
    store.write("local.txt", "Initial")
    server.failures["append_response"] = "lost"
    with pytest.raises(SyncError):
        engine.sync()
    store.write("local.txt", "Later")
    engine.sync()
    assert tracked(store).base_text == "Later"
    assert len(server.client().inventory()) == 1
    assert server.counts["append"] == 2


def test_lock_contention_becomes_operational_error(setup):
    server, store, engine = setup
    with store.lock():
        with pytest.raises(SyncError):
            engine.sync()


def test_no_uidplus_replacement_keeps_only_target_flagged(setup):
    server, store, engine = setup
    server.uidplus = False
    old_uid = add(server, "Title\nbase")
    unrelated = add(server, "Unrelated")
    server.boxes["Notes"][unrelated][1].add("\\Deleted")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nedit")
    engine.sync()
    assert server.boxes["Notes"][old_uid][1] == {"\\Deleted"}
    assert server.boxes["Notes"][unrelated][1] == {"\\Deleted"}
    assert len(server.client().inventory()) == 1
    assert "expunge" not in server.history


def test_fixture_markers_block_resolving():
    from pathlib import Path

    from applenotes.engine import has_markers

    assert has_markers((Path(__file__).parent / "fixtures/conflict.txt").read_text())


def test_unchanged_mtime_does_not_hide_edits(setup):
    import os

    server, store, engine = setup
    add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    path = store.path(record.path)
    before = path.stat()
    store.write(record.path, "Title\nedit")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    engine.sync()
    assert tracked(store).base_text == "Title\nedit"


def test_formatting_only_html_change_does_not_upload(setup):
    server, store, engine = setup
    uid = server.add(
        b"Content-Type: text/html\nX-Universally-Unique-Identifier: stable\n\n"
        b"<div>Title</div><div>Body</div>"
    )
    engine.sync()
    raw = server.boxes["Notes"].pop(uid)[0]
    server.add(raw.replace(b"Body", b"<b>Body</b>"))
    engine.sync()
    assert tracked(store).base_text == "Title\nBody"
    assert server.counts["append"] == 0


def test_link_preserves_existing_text_conflict(setup):
    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal")
    new_uid = replace(server, uid, "Title\nremote")
    engine.sync()
    original = tracked(store).conflict
    engine.link(record.path, new_uid)
    assert tracked(store).conflict == original
    assert engine.sync().conflicts == 1
    assert server.counts["append"] == 0


def test_conflict_command_redacts_password(setup):
    import sys

    server, store, engine = setup
    uid = add(server, "Title\nbase")
    engine.sync()
    record = tracked(store)
    store.write(record.path, "Title\nlocal")
    replace(server, uid, "Title\nremote")
    engine.settings.merge_command = [
        sys.executable,
        "-c",
        (
            'import sys; sys.stdout.write("<<<<<<< local\\nlocal\\n=======\\n'
            'remote\\n>>>>>>> remote\\n"); sys.exit(1)'
        ),
        "{local}",
        "{base}",
        "{remote}",
        "secret",
    ]
    engine.sync()
    assert tracked(store).conflict.command[-1] == "[redacted]"
    assert "secret" not in store.state_path.read_text()


def test_deleting_one_of_identical_notes_keeps_other(setup):
    server, store, engine = setup
    removed = add(server, "Identical")
    retained = add(server, "Identical")
    engine.sync()
    original = {n.uid: n.path for n in store.load().notes.values()}
    del server.boxes["Notes"][removed]
    engine.sync()
    assert store.read(original[removed]) is None
    assert store.read(original[retained]) == "Identical"
    assert list(server.client().inventory()) == [retained]
