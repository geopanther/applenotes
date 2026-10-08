import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def script():
    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_versions_and_workflow_guards():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    config = tomllib.loads((ROOT / ".bumpversion.toml").read_text())
    from applenotes import __version__

    assert Version(project["project"]["version"]) == Version(__version__)
    assert config["tool"]["bumpversion"]["current_version"] == __version__
    assert project["project"]["requires-python"] == ">=3.14"
    assert project["tool"]["tox"]["env_list"] == ["py314"]
    for name in ["deploy-test.yml", "deploy-prod.yml"]:
        text = (ROOT / ".github/workflows" / name).read_text()
        assert "id-token: write" in text
        assert "attestations: true" in text
        assert "environment:" in text
        for action in re.findall(r"uses: (\S+)", text):
            assert re.search(r"@[0-9a-f]{40}$", action)


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc1", "0.1.0-rc12"])
def test_release_versions(version):
    assert script().validate_version(version) == version
    assert Version(version).release == tuple(map(int, version.split("-")[0].split(".")))


@pytest.mark.parametrize(
    "version", ["v1.2.3", "1.2", "1.2.3rc1", "1.2.3-beta1", "1.2.3-rc0", "01.2.3", "x; echo"]
)
def test_invalid_release_versions(version):
    with pytest.raises(ValueError):
        script().validate_version(version)


def test_rc_changelog_promotion(tmp_path):
    release = script()
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## Unreleased\n\n## [0.2.0-rc1] - 2026-10-08\n\n### Added\n\n- Notes.\n"
    )
    release.clean_rc_changelog(path)
    text = path.read_text()
    assert "- Notes." in text and "rc1" not in text
    assert text.count("## Unreleased") == 1
    release.clean_rc_changelog(path)
    assert path.read_text() == text


def test_release_git_guards(tmp_path):
    release = script()
    with pytest.raises(release.ReleaseError):
        release.guard(tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Initial",
        ],
        check=True,
        capture_output=True,
    )
    release.guard(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty")
    with pytest.raises(release.ReleaseError):
        release.guard(tmp_path)
    (tmp_path / "dirty.txt").unlink()
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature"], check=True, capture_output=True
    )
    with pytest.raises(release.ReleaseError):
        release.guard(tmp_path)


def test_actual_candidate_and_final_bumps(tmp_path):
    import shutil
    import sys

    tool = shutil.which("bump-my-version") or str(Path(sys.executable).parent / "bump-my-version")
    assert Path(tool).exists()
    for relative in [
        "pyproject.toml",
        ".bumpversion.toml",
        "src/applenotes/__init__.py",
        "CHANGELOG.md",
    ]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)

    def commit():
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "Version files",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    commit()
    for version in ["0.2.0-rc1", "0.2.0-rc2", "0.2.0"]:
        subprocess.run(
            [tool, "bump", "--new-version", version, "patch"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        if "-rc" in version:
            script().clean_rc_changelog(tmp_path / "CHANGELOG.md")
            assert version not in (tmp_path / "CHANGELOG.md").read_text()
        assert script().check_version(tmp_path) == version
        commit()
    assert (tmp_path / "CHANGELOG.md").read_text().count("## [0.2.0]") == 1
