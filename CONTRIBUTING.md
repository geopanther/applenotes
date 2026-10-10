# Contributing

Use Python 3.14, uv, Git and the locked development environment:

```bash
brew install osv-scanner
uv sync --locked --group dev
uv run pre-commit install
uv run pre-commit run --all-files
uv run ty check src/appletextnotes
uv run tox -e py314
uv build
```

OSV-Scanner must be on `PATH`. On other platforms, install it using a package
manager or an [official release](https://github.com/google/osv-scanner/releases).
Its pre-commit hook uses the installed executable, so no Go environment or scanner
build is needed. CI installs the pinned 2.6.0 Linux binary and verifies its SHA-256.
The scanner runs on every commit and push to catch newly disclosed vulnerabilities
even when `uv.lock` is unchanged. Homebrew manages the local scanner version.

Existing clones only need to run `uv run pre-commit run --all-files` after updating
the hook configuration; recreating environments or running `pre-commit clean` is
unnecessary.

Write behavior tests before production changes. Tests must never load a real
`.env`, credentials or server. The autouse fixture clears `APPLETEXTNOTES_` variables
and changes into a temporary directory. Use explicit synthetic settings, the
independent stateful server in `tests/mock_server.py`, and the fragmented loopback
IMAP fixture for transport tests. Loopback tests require permission to bind a
localhost socket; they do not contact an external mailbox.

Keep typed interfaces and Pydantic models for domain and persisted data. Use
four-space indentation, Ruff formatting and LF line endings. Preserve intentional
whitespace in MIME fixtures; `tests/fixtures` is excluded from generic whitespace,
conflict-marker and documentation formatting hooks. Test helpers are excluded
from the test-name hook. Bandit excludes B101 for internal assertions; the only
subprocess exceptions are the explicitly configured, shell-free merge adapter.

Tox runs pytest, requires 90% combined coverage, and independently checks at least
90% branch coverage. Every merge, deletion and recovery regression must pass even
when total coverage is high. New state changes need strict load/save validation
and recovery tests for each new checkpoint. Tests for races should schedule an
external edit using server command occurrence counters rather than sleeping.

Pre-commit checks YAML/JSON/TOML, test names, whitespace and conflict markers;
Ruff, Prettier, ty, Bandit, OSV scanning and Gitlint enforce development conventions.
Tox also runs on pre-push. CI uses pinned GitHub Action commits and the same lockfile
and Python version. Gitlint runs from the locked development environment using
`gitlint-core` and a patched `sh` dependency, avoiding the wrapper package's
legacy dependency pin. Run the pre-push checks before requesting review.

Use concise imperative commit subjects and keep related changes together. Add
user-facing changes under `## Unreleased` in `CHANGELOG.md`. Do not publish or
change versions as part of a routine feature PR. See [releasing](docs/releasing.md).
