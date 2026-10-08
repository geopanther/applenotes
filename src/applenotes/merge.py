"""A noninteractive, shell-free three-way merge adapter."""

# Execute only the explicitly configured noninteractive merge tool.
import subprocess  # nosec B404
import tempfile
from pathlib import Path

from applenotes.models import MergeResult
from applenotes.settings import validate_command


class MergeError(Exception):
    pass


class Merger:
    def __init__(self, command: list[str], directory: Path, timeout: float = 30):
        self.command = validate_command(command)
        self.directory = directory
        self.timeout = timeout

    def merge(self, local: str, base: str, remote: str) -> MergeResult:
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="merge-", dir=self.directory) as temporary:
            paths = {name: str(Path(temporary) / name) for name in ("local", "base", "remote")}
            for name, content in (("local", local), ("base", base), ("remote", remote)):
                Path(paths[name]).write_bytes(content.encode("utf-8"))
            argv = [argument.format(**paths) for argument in self.command]
            try:
                result = subprocess.run(  # nosec B603
                    argv,
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
                text = result.stdout.decode("utf-8")
            except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
                raise MergeError(
                    "merge tool failed; original versions have been preserved"
                ) from error
            git = Path(argv[0]).name == "git" and len(argv) > 1 and argv[1] == "merge-file"
            if result.returncode not in (0, 1) and not (git and 1 < result.returncode <= 127):
                raise MergeError(f"merge tool failed with exit status {result.returncode}")
            return MergeResult(outcome="clean" if result.returncode == 0 else "conflict", text=text)
