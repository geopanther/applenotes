import pytest

from applenotes.planner import decide


@pytest.mark.parametrize(
    ("base", "local", "remote", "action"),
    [
        ("a", "a", "a", "none"),
        ("a", "b", "a", "upload"),
        ("a", "a", "b", "download"),
        ("a", "b", "b", "advance"),
        ("a", "b", "c", "merge"),
        ("a", None, "a", "delete_remote"),
        ("a", "a", None, "delete_local"),
        ("a", None, "b", "conflict"),
        ("a", "b", None, "conflict"),
        ("a", None, None, "tombstone"),
    ],
)
def test_reconciliation(base, local, remote, action):
    assert decide(base, local, remote) == action
