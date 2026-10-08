import json

import pytest

from applenotes.cli import main
from applenotes.models import State
from applenotes.storage import Storage
from tests.mock_server import MockServer


@pytest.mark.parametrize("args", [["--help"], ["--version"], ["status", "--help"]])
def test_help(args, capsys):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 0
    assert capsys.readouterr().out


def test_offline_status_and_discovery(tmp_path, capsys):
    Storage(tmp_path).initialize(State())
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    nested = tmp_path / "nested"
    nested.mkdir()
    assert main(["--workspace", str(nested), "status"]) == 0


def test_invalid_and_secret_free_errors(tmp_path, monkeypatch, capsys):
    assert main(["sync"]) == 2
    error = capsys.readouterr().err
    for setting in ["IMAP_SERVER", "IMAP_USERNAME", "IMAP_PASSWORD"]:
        assert f"APPLENOTES_{setting}" in error
    assert "required" in error
    monkeypatch.setenv("APPLENOTES_IMAP_PASSWORD", "DO-NOT-PRINT")
    monkeypatch.setenv("APPLENOTES_INDENT_SPACES", "invalid")
    assert main(["sync"]) == 2
    error = capsys.readouterr().err
    assert "APPLENOTES_INDENT_SPACES" in error
    assert "valid integer" in error
    assert "DO-NOT-PRINT" not in error


def test_missing_password_is_specific(monkeypatch, capsys):
    monkeypatch.setenv("APPLENOTES_IMAP_SERVER", "test")
    monkeypatch.setenv("APPLENOTES_IMAP_USERNAME", "user")
    assert main(["init"]) == 2
    error = capsys.readouterr().err
    assert "APPLENOTES_IMAP_PASSWORD" in error
    assert "APPLENOTES_IMAP_SERVER" not in error
    assert "APPLENOTES_IMAP_USERNAME" not in error


def test_multiple_invalid_settings(monkeypatch, capsys):
    monkeypatch.setenv("APPLENOTES_INDENT_SPACES", "0")
    monkeypatch.setenv("APPLENOTES_IMAP_PORT", "65536")
    assert main(["init"]) == 2
    error = capsys.readouterr().err
    assert "APPLENOTES_INDENT_SPACES" in error
    assert "greater than or equal to 1" in error
    assert "APPLENOTES_IMAP_PORT" in error
    assert "less than or equal to 65535" in error


@pytest.mark.parametrize("source", ["environment", "dotenv", "cli"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("DO-NOT-PRINT", "Invalid JSON"),
        ('["DO-NOT-PRINT", "{local}"]', "local, base, and remote placeholders"),
        ('["tool", "{local}", "{base}", "{remote}", 123]', "valid string"),
    ],
)
def test_invalid_merge_command(source, value, reason, tmp_path, monkeypatch, capsys):
    args = ["init"]
    if source == "environment":
        monkeypatch.setenv("APPLENOTES_MERGE_COMMAND", value)
    elif source == "dotenv":
        dotenv = tmp_path / "custom.env"
        dotenv.write_text(f"APPLENOTES_MERGE_COMMAND='{value}'\n")
        args = ["--env-file", str(dotenv), *args]
    else:
        args = ["--merge-command", value, *args]
    assert main(args) == 2
    error = capsys.readouterr().err
    assert "APPLENOTES_MERGE_COMMAND" in error
    assert reason in error
    assert "DO-NOT-PRINT" not in error


def test_invalid_password_value_is_not_printed(monkeypatch, capsys):
    from applenotes.settings import Settings

    class InvalidSettings(Settings):
        def __init__(self, **values):
            super().__init__(imap_password={"secret": "DO-NOT-PRINT"}, _env_file=None)

    monkeypatch.setattr("applenotes.cli.Settings", InvalidSettings)
    assert main(["init"]) == 2
    error = capsys.readouterr().err
    assert "APPLENOTES_IMAP_PASSWORD" in error
    assert "DO-NOT-PRINT" not in error


def test_network_commands(tmp_path, monkeypatch, capsys):
    server = MockServer()
    monkeypatch.setenv("APPLENOTES_IMAP_SERVER", "test")
    monkeypatch.setenv("APPLENOTES_IMAP_USERNAME", "user")
    monkeypatch.setenv("APPLENOTES_IMAP_PASSWORD", "secret")
    monkeypatch.setattr("applenotes.cli.IMAPTransport.connect", lambda settings: server.client())
    assert main(["init"]) == 0
    (tmp_path / "local.txt").write_text("Title\nbody")
    assert main(["push", "--dry-run"]) == 0
    assert server.boxes["Notes"] == {}
    assert main(["push"]) == 0
    assert len(server.boxes["Notes"]) == 1
    assert main(["pull"]) == 0
    assert main(["sync"]) == 0
    assert main(["resolve", "local.txt"]) == 2
    assert main(["link", "local.txt", "--remote-uid", "999"]) == 2
    assert main(["init"]) == 1
    assert "secret" not in capsys.readouterr().err


def test_module_entry_point(monkeypatch, capsys):
    import runpy

    monkeypatch.setattr("sys.argv", ["applenotes", "--version"])
    with pytest.raises(SystemExit) as error:
        runpy.run_module("applenotes", run_name="__main__")
    assert error.value.code == 0
    from applenotes import __version__

    assert capsys.readouterr().out.strip() == __version__


def test_status_labels_and_failure(tmp_path, capsys):
    from applenotes.models import Conflict, NoteRecord

    store = Storage(tmp_path)
    store.initialize(
        State(
            notes={
                "a": NoteRecord(local_id="a", path="changed.txt", base_text="before"),
                "b": NoteRecord(local_id="b", path="missing.txt"),
                "c": NoteRecord(local_id="c", path="deleted.txt", deleted=True),
                "d": NoteRecord(
                    local_id="d",
                    path="conflict.txt",
                    conflict=Conflict(kind="identity", base="", local="", remote=None),
                ),
            }
        )
    )
    store.write("changed.txt", "after")
    assert main(["status"]) == 3
    text = capsys.readouterr().out
    for label in ["modified", "missing", "deleted", "conflict"]:
        assert label in text
    store.state_path.write_text("{bad")
    assert main(["status"]) == 1


def test_cli_env_file_and_connection_failure(tmp_path, monkeypatch):
    from applenotes.transport import TransportError

    dotenv = tmp_path / "custom.env"
    dotenv.write_text(
        "APPLENOTES_IMAP_SERVER=test\nAPPLENOTES_IMAP_USERNAME=user\nAPPLENOTES_IMAP_PASSWORD=secret\n"
    )

    def fail(settings):
        raise TransportError("connection failed")

    monkeypatch.setattr("applenotes.cli.IMAPTransport.connect", fail)
    assert main(["--env-file", str(dotenv), "init"]) == 1
