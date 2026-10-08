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
    monkeypatch.setenv("APPLENOTES_IMAP_PASSWORD", "DO-NOT-PRINT")
    monkeypatch.setenv("APPLENOTES_INDENT_SPACES", "invalid")
    assert main(["sync"]) == 2
    assert "DO-NOT-PRINT" not in capsys.readouterr().err


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
