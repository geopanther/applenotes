"""Exercise release helpers without touching a real remote, PR, or registry."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release_commands(tmp_path, monkeypatch):
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    command = (
        f"#!{sys.executable}\n"
        + """import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path("commands.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "gh":
    if args[:2] == ["repo", "view"]:
        print(os.environ.get("TEST_DEFAULT_BRANCH", "main"))
    elif args[:2] == ["pr", "checks"]:
        if "--watch" in args:
            sys.exit(int(os.environ.get("TEST_CHECK_RESULT", "0")))
        count_path = Path("check-count")
        count = int(count_path.read_text()) if count_path.exists() else 0
        count_path.write_text(str(count + 1))
        sys.exit(1 if count < int(os.environ.get("TEST_CHECK_DELAY", "1")) else 8)
elif name == "git":
    if args[:2] == ["branch", "--show-current"]:
        print(os.environ.get("TEST_BRANCH", "main"))
    elif args[:1] == ["rev-parse"]:
        if args[1] == "HEAD":
            print("a" * 40)
        elif args[1].startswith("origin/"):
            print(os.environ.get("TEST_REMOTE_SHA", "a" * 40))
        else:
            sys.exit(0 if os.environ.get("TEST_TAG_EXISTS") else 1)
elif name == "bump-my-version":
    print(os.environ.get("TEST_VERSION", "0.2.0-rc0"))
elif name == "uv":
    assert os.environ.get("UV_LOCKED") == "0"
    sys.exit(int(os.environ.get("TEST_SYNC_RESULT", "0")))
elif name == "python":
    os.execv(sys.executable, [sys.executable, *args])
"""
    )
    for name in ["gh", "git", "bump-my-version", "uv", "python", "sleep"]:
        path = bin_path / name
        path.write_text(command)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_path}{os.pathsep}{os.environ['PATH']}")
    (tmp_path / "scripts").mkdir()
    cleaner = ROOT / "scripts/revert_changelog_rc.py"
    if cleaner.exists():
        shutil.copyfile(cleaner, tmp_path / "scripts/revert_changelog_rc.py")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n## [0.2.0-rc0] - 2026-10-08\n\n- Notes.\n"
    )

    def run(name):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / name)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        log = tmp_path / "commands.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return run


@pytest.mark.parametrize(
    ("version", "branch", "title"),
    [
        ("0.2.0-rc0", "chore/bump-0.2.0-rc0", "Bumping version to 0.2.0-rc0"),
        ("0.2.0", "chore/release-0.2.0", "Release 0.2.0"),
    ],
)
def test_merge_bump_workflow(release_commands, monkeypatch, tmp_path, version, branch, title):
    monkeypatch.setenv("TEST_VERSION", version)
    result, calls = release_commands("merge-bump.sh")
    assert result.returncode == 0, result.stderr
    assert ["git", "checkout", "-b", branch] in calls
    assert ["uv", "sync", "--all-groups"] in calls
    assert [
        "git",
        "add",
        "pyproject.toml",
        "src/appletextnotes/__init__.py",
        ".bumpversion.toml",
        "CHANGELOG.md",
        "uv.lock",
    ] in calls
    assert ["git", "commit", "--no-edit", "-m", f"Bump version: {version}"] in calls
    assert ["git", "push", "--set-upstream", "origin", branch] in calls
    assert ["gh", "pr", "create", "--title", title, "--body", title] in calls
    assert calls[-2:] == [
        ["gh", "pr", "checks", "--watch", "--interval", "1", "--fail-fast"],
        ["gh", "pr", "merge", "--squash", "--delete-branch"],
    ]
    assert ("## [0.2.0-rc0]" in (tmp_path / "CHANGELOG.md").read_text()) == ("-rc" not in version)


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("TEST_BRANCH", "feature", "Must be on"),
        ("TEST_REMOTE_SHA", "b" * 40, "differs from remote"),
        ("TEST_VERSION", "", "Could not determine current version"),
        ("TEST_SYNC_RESULT", "1", "Command failed"),
        ("TEST_CHECK_DELAY", "30", "No CI checks appeared"),
        ("TEST_CHECK_RESULT", "1", "Command failed"),
    ],
)
def test_merge_bump_stops_on_failure(release_commands, monkeypatch, setting, value, message):
    monkeypatch.setenv(setting, value)
    result, calls = release_commands("merge-bump.sh")
    assert result.returncode != 0
    assert message in result.stderr
    assert not any(call[:3] == ["gh", "pr", "merge"] for call in calls)
    if setting in {"TEST_BRANCH", "TEST_REMOTE_SHA", "TEST_VERSION", "TEST_SYNC_RESULT"}:
        assert not any(call[:2] == ["git", "push"] for call in calls)


def test_merge_bump_uses_default_branch(release_commands, monkeypatch):
    monkeypatch.setenv("TEST_DEFAULT_BRANCH", "trunk")
    monkeypatch.setenv("TEST_BRANCH", "trunk")
    result, calls = release_commands("merge-bump.sh")
    assert result.returncode == 0, result.stderr
    assert ["git", "fetch", "origin", "trunk"] in calls


@pytest.mark.parametrize("version", ["0.2.0-rc0", "0.2.0"])
def test_publish_release_tags_after_updating_default_branch(release_commands, monkeypatch, version):
    monkeypatch.setenv("TEST_DEFAULT_BRANCH", "trunk")
    monkeypatch.setenv("TEST_VERSION", version)
    result, calls = release_commands("publish-release.sh")
    assert result.returncode == 0, result.stderr
    assert calls[1:3] == [["git", "checkout", "trunk"], ["git", "pull"]]
    assert calls[-2:] == [["git", "tag", f"v{version}"], ["git", "push", "origin", f"v{version}"]]
    assert ("deploy-test.yml" if "-rc" in version else "deploy-prod.yml") in result.stdout


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [("TEST_TAG_EXISTS", "1", "already exists"), ("TEST_VERSION", "", "Could not determine")],
)
def test_publish_release_stops_before_tagging(
    release_commands, monkeypatch, setting, value, message
):
    monkeypatch.setenv(setting, value)
    result, calls = release_commands("publish-release.sh")
    assert result.returncode != 0
    assert message in result.stderr
    assert not any(call[:2] in [["git", "tag"], ["git", "push"]] for call in calls)
