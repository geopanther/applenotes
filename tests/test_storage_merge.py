import json
import sys

import pytest

from appletextnotes.merge import MergeError, Merger
from appletextnotes.models import State
from appletextnotes.settings import DEFAULT_MERGE_COMMAND
from appletextnotes.storage import Storage, StorageError, filename


def test_state_atomic_and_validation(tmp_path):
    store = Storage(tmp_path)
    with pytest.raises(StorageError):
        store.load()
    store.initialize(State())
    assert store.load() == State()
    with pytest.raises(StorageError):
        store.initialize(State())
    store.state_path.write_text("{")
    with pytest.raises(StorageError):
        store.load()
    store.state_path.write_text(json.dumps({"schema_version": 2}))
    with pytest.raises(StorageError):
        store.load()
    assert store.state_path.read_text() == '{"schema_version": 2}'


def test_lock_paths_and_cleanup(tmp_path):
    store = Storage(tmp_path)
    store.initialize(State())
    with store.lock():
        with pytest.raises(StorageError):
            with store.lock():
                pass
    assert not (store.meta / "lock").exists()
    for path in ["../escape", "/absolute", ".appletextnotes/state.json"]:
        with pytest.raises(StorageError):
            store.path(path)
    (tmp_path / "bad.txt").symlink_to("/etc/passwd")
    with pytest.raises(StorageError):
        store.read("bad.txt")
    (tmp_path / "bad.txt").unlink()
    store.write("good.txt", "hi")
    assert store.read("good.txt") == "hi"
    assert store.read("absent.txt") is None
    assert store.discover() == ["good.txt"]
    store.write("good.remote-conflict.txt", "conflict")
    assert store.discover() == ["good.txt"]


def test_atomic_failure_preserves_old(tmp_path, monkeypatch):
    store = Storage(tmp_path)
    store.initialize(State())
    original = store.state_path.read_bytes()

    def fail(*args):
        raise OSError("disk")

    monkeypatch.setattr("appletextnotes.storage.os.replace", fail)
    with pytest.raises(OSError):
        store.save(State(uidvalidity=2))
    assert store.state_path.read_bytes() == original
    assert list(store.meta.iterdir()) == [store.state_path]


@pytest.mark.parametrize("text", ["", "../../bad:<>/\\\0", "同名", "A" * 1000])
def test_filenames(text):
    name = filename(text, "id")
    assert name.endswith("--id.txt")
    assert "/" not in name and "\\" not in name and len(name.encode()) <= 240
    assert filename(text, "other") != name


def test_git_clean_and_conflicted(tmp_path):
    merge = Merger(DEFAULT_MERGE_COMMAND, tmp_path)
    result = merge.merge("A\nb\nc\n", "a\nb\nc\n", "a\nb\nC\n")
    assert result.outcome == "clean"
    assert result.text == "A\nb\nC\n"
    result = merge.merge("local\n", "base\n", "remote\n")
    assert result.outcome == "conflict"
    for marker in ["<<<<<<<", "|||||||", "=======", ">>>>>>>"]:
        assert marker in result.text
    assert list(tmp_path.iterdir()) == []


def custom(tmp_path, code, **kwargs):
    command = [sys.executable, "-c", code, "{local}", "{base}", "{remote}"]
    return Merger(command, tmp_path, **kwargs)


@pytest.mark.parametrize("exitcode", [0, 1])
def test_custom_contract_and_literal_arguments(tmp_path, exitcode):
    parent = tmp_path / "space ; $(echo bad)"
    parent.mkdir()
    tool = custom(
        parent,
        (
            "import pathlib,sys; sys.stdout.write(pathlib.Path(sys.argv[1]).read_text()); "
            f"sys.exit({exitcode})"
        ),
    )
    result = tool.merge("hello", "", "")
    assert result.text == "hello"
    assert result.outcome == ("clean" if exitcode == 0 else "conflict")


@pytest.mark.parametrize(
    "code",
    [
        "import sys; sys.exit(2)",
        'import sys; sys.stdout.buffer.write(b"\\xff")',
        "import time; time.sleep(2)",
    ],
)
def test_merge_failures(tmp_path, code):
    with pytest.raises(MergeError):
        custom(tmp_path, code, timeout=0.05).merge("a", "b", "c")
    assert list(tmp_path.iterdir()) == []


def test_missing_merge_executable(tmp_path):
    with pytest.raises(MergeError):
        Merger(["no-such-executable", "{local}", "{base}", "{remote}"], tmp_path).merge("", "", "")


def test_metadata_symlinks_rejected(tmp_path):
    (tmp_path / ".appletextnotes").symlink_to(tmp_path / "other")
    with pytest.raises(StorageError):
        Storage(tmp_path)
    (tmp_path / ".appletextnotes").unlink()
    (tmp_path / ".appletextnotes").mkdir()
    (tmp_path / ".appletextnotes/state.json").symlink_to("/etc/passwd")
    with pytest.raises(StorageError):
        Storage(tmp_path)


@pytest.mark.parametrize(
    ("base", "local", "remote", "clean"),
    [
        ("a\nb\nc\nd\n", "a\nb\nc\nd\n", "a\nb\nc\nd\n", True),
        ("a\nb\nc\nd\n", "insert\na\nb\nc\nd\n", "a\nb\nc\nd\nend\n", True),
        ("a\nb\nc\nd\n", "b\nc\nd\n", "a\nb\nc\n", True),
        ("a\nb\nc\nd\n", "A\nb\nc\nd\n", "a\nB\nc\nd\n", False),
        ("a\n", "local\na\n", "remote\na\n", False),
        ("a\n", "", "A\n", False),
        ("a\n", "", "", True),
        ("", "local\n", "remote\n", False),
        ("a", "a\n", "a", True),
        ("ü\nx\ny\n", "Ü\nx\ny\n", "ü\nx\nY\n", True),
        ("a\n    b\nc\n", "A\n    b\nc\n", "a\n\tb\nc\n", False),
        ("a\nb\nc\nd\ne\n", "A\nb\nc\nd\nE\n", "X\nb\nc\nd\nY\n", False),
    ],
)
def test_merge_edge_cases(tmp_path, base, local, remote, clean):
    result = Merger(DEFAULT_MERGE_COMMAND, tmp_path).merge(local, base, remote)
    assert (result.outcome == "clean") is clean
    if not clean:
        assert "<<<<<<<" in result.text and ">>>>>>>" in result.text
