#!/usr/bin/env python3
"""Guarded release preparation and explicit tag publication; no automatic merge."""

import argparse
import re
import subprocess
import tomllib
from pathlib import Path


class ReleaseError(Exception):
    pass


def run(root: Path, *argv: str) -> str:
    try:
        return subprocess.run(
            argv, cwd=root, text=True, capture_output=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseError(f"Release command failed: {argv[0]}") from error


def validate_version(version: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-rc[1-9]\d*)?", version):
        raise ValueError("Use MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rcN (N >= 1)")
    return version


def clean_rc_changelog(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"^## \[\d+\.\d+\.\d+-rc\d+\] - \d{4}-\d{2}-\d{2}\n", "", text, flags=re.MULTILINE
    )
    path.write_text(re.sub(r"\n{3,}", "\n\n", text), encoding="utf-8")


def guard(root: Path) -> None:
    if run(root, "git", "status", "--porcelain"):
        raise ReleaseError("Release preparation requires a clean working tree")
    if run(root, "git", "branch", "--show-current") != "main":
        raise ReleaseError("Release preparation requires the main branch")


def check_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())
    config = tomllib.loads((root / ".bumpversion.toml").read_text())
    version = validate_version(project["project"]["version"])
    source = (root / "src/applenotes/__init__.py").read_text()
    if (
        f'__version__ = "{version}"' not in source
        or config["tool"]["bumpversion"]["current_version"] != version
    ):
        raise ReleaseError("Package and bump configuration versions disagree")
    if "-rc" not in version and f"## [{version}] - " not in (root / "CHANGELOG.md").read_text():
        raise ReleaseError("Final releases require a dated changelog section")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("version", type=validate_version)
    tag = commands.add_parser("tag")
    tag.add_argument(
        "--push", action="store_true", help="Create and push a tag; triggers publishing"
    )
    commands.add_parser("check")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "check":
            print(check_version(root))
            return 0
        guard(root)
        if args.command == "prepare":
            run(root, "git", "checkout", "-b", f"chore/release-{args.version}")
            run(
                root,
                "uv",
                "run",
                "--locked",
                "bump-my-version",
                "bump",
                "--new-version",
                args.version,
                "patch",
            )
            if "-rc" in args.version:
                clean_rc_changelog(root / "CHANGELOG.md")
            run(root, "uv", "lock")
            print(
                "Prepared release files. Review, commit, and open a PR; "
                "CI must pass before merging."
            )
            return 0
        version = check_version(root)
        release_tag = f"v{version}"
        if release_tag in run(root, "git", "tag", "--list").splitlines():
            raise ReleaseError("Release tag already exists")
        if not args.push:
            print(f"Ready for {release_tag}; rerun with --push to trigger the publishing workflow")
            return 0
        run(root, "git", "fetch", "origin", "main")
        if run(root, "git", "rev-parse", "HEAD") != run(root, "git", "rev-parse", "origin/main"):
            raise ReleaseError("Local main differs from origin/main")
        run(root, "git", "tag", "-a", release_tag, "-m", f"Release {version}")
        run(root, "git", "push", "origin", release_tag)
        return 0
    except ReleaseError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
