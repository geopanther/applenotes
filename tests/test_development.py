import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def script():
    spec = importlib.util.spec_from_file_location(
        "revert_changelog_rc", ROOT / "scripts/revert_changelog_rc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_versions_and_workflow_guards():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    config = tomllib.loads((ROOT / ".bumpversion.toml").read_text())
    from appletextnotes import __version__

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


@pytest.mark.parametrize("heading", ["[0.2.0-rc0]", "0.2.0-rc1"])
def test_rc_changelog_promotion(tmp_path, heading):
    release = script()
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        f"# Changelog\n\n## Unreleased\n\n## {heading} - 2026-10-08\n\n### Added\n\n- Notes.\n"
        "\n## [0.1.0] - 2026-10-01\n\n- Previous release.\n"
    )
    assert release.main() == 0
    text = path.read_text()
    assert "- Notes." in text and heading not in text
    assert text.count("## Unreleased") == 1
    assert "## [0.1.0] - 2026-10-01" in text
    assert release.main() == 0
    assert path.read_text() == text


def test_rc_changelog_missing_file():
    assert script().main() == 0


def test_actual_candidate_and_final_bumps(tmp_path):
    import shutil
    import sys

    tool = shutil.which("bump-my-version") or str(Path(sys.executable).parent / "bump-my-version")
    assert Path(tool).exists()
    for relative in [
        "pyproject.toml",
        ".bumpversion.toml",
        "src/appletextnotes/__init__.py",
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
    for args, version in [
        (["--new-version", "0.2.1", "patch"], "0.2.1"),
        (["minor"], "0.3.0-rc0"),
        (["pre_n"], "0.3.0-rc1"),
        (["pre_l"], "0.3.0"),
    ]:
        subprocess.run(
            [tool, "bump", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        if "-rc" in version:
            assert script().main() == 0
            assert version not in (tmp_path / "CHANGELOG.md").read_text()
        project = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        config = tomllib.loads((tmp_path / ".bumpversion.toml").read_text())
        assert project["project"]["version"] == version
        assert config["tool"]["bumpversion"]["current_version"] == version
        assert (
            f'__version__ = "{version}"'
            in (tmp_path / "src/appletextnotes/__init__.py").read_text()
        )
        commit()
    assert (tmp_path / "CHANGELOG.md").read_text().count("## [0.3.0]") == 1
