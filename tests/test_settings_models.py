import json

import pytest
from pydantic import ValidationError

from applenotes.models import Conflict, NoteRecord, State, digest
from applenotes.settings import DEFAULT_MERGE_COMMAND, Settings


def test_offline_defaults_and_network_validation():
    settings = Settings(_env_file=None)
    assert settings.imap_port == 993
    assert settings.imap_folder == "Notes"
    assert settings.indent_spaces == 4
    assert settings.merge_command == DEFAULT_MERGE_COMMAND
    with pytest.raises(ValueError):
        settings.require_network()
    settings = Settings(
        imap_server="host", imap_username="user", imap_password="SECRET", _env_file=None
    )
    settings.require_network()
    assert "SECRET" not in repr(settings)
    assert "SECRET" not in settings.model_dump_json()
    assert settings.identity().username == "user"


def test_precedence(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("APPLENOTES_IMAP_SERVER=dotenv\nAPPLENOTES_IMAP_PASSWORD=secret\n")
    monkeypatch.setenv("APPLENOTES_IMAP_SERVER", "environment")
    assert Settings(_env_file=dotenv).imap_server == "environment"
    assert Settings(imap_server="cli", _env_file=dotenv).imap_server == "cli"
    monkeypatch.setenv(
        "APPLENOTES_MERGE_COMMAND", json.dumps(["tool", "{local}", "{base}", "{remote}"])
    )
    assert Settings(_env_file=None).merge_command[0] == "tool"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"indent_spaces": 0},
        {"timeout_seconds": 0},
        {"imap_port": 65536},
        {"imap_security": "none"},
        {"merge_timeout_seconds": -1},
        {"merge_command": ["tool", "{local}", "{remote}"]},
        {"merge_command": ["tool", "{local}", "{base}", "{remote}", "{unknown}"]},
        {"merge_command": []},
        {"merge_command": "not json"},
    ],
)
def test_invalid_settings(kwargs):
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None, **kwargs)


def test_state_roundtrip_validation():
    record = NoteRecord(local_id="abc", path="ü--abc.txt", base_text="😀\n", uid=2)
    assert record.base_hash == digest("😀\n")
    state = State(notes={"abc": record})
    assert State.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        State(schema_version=999)
    with pytest.raises(ValidationError):
        State(notes={"other": record})
    with pytest.raises(ValidationError):
        State(notes={"abc": record, "def": NoteRecord(local_id="def", path="z.txt", uid=2)})
    with pytest.raises(ValidationError):
        NoteRecord(local_id="x", path="../escape.txt")
    with pytest.raises(ValidationError):
        Conflict(kind="text", base="", local="a", remote="b", remote_uid=None)


@pytest.mark.parametrize("value", ["", "\n", "a\0b"])
def test_unsafe_settings(value):
    with pytest.raises(ValidationError):
        Settings(imap_folder=value, _env_file=None)


def test_invalid_command_null_and_literal_braces():
    with pytest.raises(ValidationError):
        Settings(merge_command=["tool\0", "{local}", "{base}", "{remote}"], _env_file=None)
    s = Settings(
        merge_command=["tool", "{{literal}}", "{local}", "{base}", "{remote}"], _env_file=None
    )
    assert s.merge_command[1] == "{{literal}}"


def test_invalid_state_hash_ids_paths_and_pending():
    from applenotes.models import PendingOperation

    with pytest.raises(ValidationError):
        NoteRecord(local_id="x", path="x.txt", base_text="one", base_hash=digest("two"))
    with pytest.raises(ValidationError):
        NoteRecord(local_id="../x", path="x.txt")
    with pytest.raises(ValidationError):
        State(
            pending={
                "op": PendingOperation(
                    operation_id="op", note_id="missing", action="delete_local", uidvalidity=1
                )
            }
        )
    with pytest.raises(ValidationError):
        PendingOperation(operation_id="op", note_id="x", action="upload", uidvalidity=1)
    with pytest.raises(ValidationError):
        State(
            notes={
                "a": NoteRecord(local_id="a", path="A.txt"),
                "b": NoteRecord(local_id="b", path="a.txt"),
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "download"},
        {"action": "conflict", "text": "marked"},
        {"action": "delete_local", "phase": "verified"},
        {"action": "delete_remote", "remote_before": {"uid": 2, "text": "old"}, "old_uid": 3},
    ],
)
def test_pending_contract(overrides):
    from applenotes.models import PendingOperation

    values = {"operation_id": "op", "note_id": "note", "uidvalidity": 1}
    with pytest.raises(ValidationError):
        PendingOperation(**(values | overrides))
