# Changelog

All notable changes follow Semantic Versioning. Release candidates keep their
changes under Unreleased until a final release is prepared.

## Unreleased

### Fixed

- Configuration errors name missing or invalid settings and explain validation
  failures without printing credential values.

### Added

- Hidden interactive password prompt for network commands when
  `APPLENOTES_IMAP_PASSWORD` is unset.
- IMAP synchronization with plain-text MIME uploads and canonical HTML import.
- Configurable three-way merges, conflict copies, identity linking, deletions,
  and checkpointed recovery in a human-readable state file.
- Python 3.14 package, isolated server tests, development and release tooling.
