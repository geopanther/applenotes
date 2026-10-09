# Changelog

All notable changes follow Semantic Versioning. Release candidates keep their
changes under Unreleased until a final release is prepared.

## Unreleased

## [0.1.0] - 2026-10-10

### Added

- Hidden interactive password prompt for network commands when
  `APPLENOTES_IMAP_PASSWORD` is unset; the password is never saved.
- CI distribution checks build wheels and source archives and validate them with
  the publisher's Twine and packaging versions before release tagging.
- Isolated tests for version bumping, RC changelog cleanup, release scripts, and
  publishing workflows.

### Changed

- Adopted the mdfluence release process: version bumps support `rc0`, and release
  helpers create branches, sync the lockfile, commit, push, open PRs, watch CI,
  squash-merge, and tag the merged version.
- Publishing workflows build distributions separately from approval-gated
  TestPyPI and PyPI publishing. Final releases create a GitHub Release with notes
  extracted from this changelog; TestPyPI also supports manual workflow runs.
- Release candidates keep their notes under Unreleased. Version and lockfile
  updates prepared `0.1.0-rc1` through `0.1.0-rc5`.
- Release documentation describes the two-stage publishing process, approval
  gates, version bumps, and package verification.
- README installation instructions use the published package with
  `uv tool install applenotes` and link to uv installation instructions.
- Git ignores local `.txt` note files and `setenv.sh`.

### Fixed

- Configuration errors name missing or invalid settings and explain validation
  failures without printing credential values.
- Wheels and source archives use core metadata 2.4 for compatibility with the
  publishing validator.
- TestPyPI verification keeps PyPI as the default index and uses
  `unsafe-best-match` so dependencies can resolve across both indexes.
- TestPyPI and PyPI verification refresh the applenotes index cache with
  `--refresh-package applenotes` to discover newly published versions.
- Removed the erroneous `0.1.0-final1` heading introduced during an RC bump so
  pending changes remain under Unreleased until the first final release.

### Initial Version - 2026-10-08

- Python 3.14 CLI for macOS and Linux with `init`, `pull`, `push`, `sync`,
  `status`, `resolve`, and `link` commands, dry runs, and JSON output.
- IMAP synchronization with TLS or verified STARTTLS, plain-text MIME uploads,
  Apple note identification headers, and canonical HTML import.
- Stable note filenames, configurable indentation normalization, and content
  comparisons independent of modification times.
- Configurable three-way merges, conflict copies, identity linking, deletions,
  and checkpointed recovery in a human-readable state file.
- Atomic state writes, workspace locking, symlink and path-escape checks,
  duplicate-upload recovery, and selective deletion without mailbox-wide expunge.
- Command-line, environment, and optional `.env` configuration, with credentials
  excluded from persisted state and offline status output.
- Isolated mock-server and loopback transport tests, coverage gates, linting,
  type checking, vulnerability scanning, and commit hooks.
- MIT-licensed package, usage and interoperability documentation, and initial
  development and release tooling with OIDC publishing and digital attestations.
