# Implementation Plan: `applenotes`

## 1. Goal and behavior

Build a Python 3.14 CLI that synchronizes IMAP notes with editable UTF-8 plain-text files. Use `uv`, Pydantic, Pydantic Settings, and tests written before production implementations.

Required behavior:

- Always convert incoming HTML to plain text before storage, comparison, or merging.
- Strip formatting, images, attachments, and other nontext content while preserving readable text.
- Upload `text/plain` MIME messages. Never regenerate HTML.
- Use the first line as the note title.
- Convert complete groups of four leading spaces into tabs; preserve remainder spaces and make the width configurable.
- Preserve actual tabs locally and remotely.
- Support a configurable merge tool, defaulting to Git’s three-way file merge.
- Write unresolved conflicts into two local copies; leave the remote note unchanged.
- Propagate deletions when the counterpart is unchanged; preserve edit-versus-delete conflicts.
- Store all durable synchronization metadata in `.applenotes/state.json`.

Existing environment files remain untouched. Tests must isolate themselves from real credentials and `.env` files.

## 2. Package and development workflow

Create `src/applenotes/`, `tests/`, synthetic MIME fixtures, contributor documentation, and release documentation.

Use Hatchling, Python `>=3.14`, a committed `uv.lock`, and a Python 3.14 development environment.

Adapt mdfluence’s inspected development configuration:

- Pre-commit checks for YAML, JSON, TOML, whitespace, line endings, conflict markers, and test names.
- Ruff linting and formatting; Prettier for documentation and configuration.
- Astral `ty`, Bandit, OSV dependency scanning, and Gitlint.
- pytest through tox, with a `py314` environment and testing at `pre-push`.
- Equivalent CI checks with pinned GitHub Actions.

Use four-space Python indentation and typed interfaces. Exclude intentional conflict-marker and whitespace fixtures from inappropriate formatting hooks.

Adopt mdfluence’s Semantic Versioning, `bump-my-version`, `Unreleased` changelog section, release candidates, helper scripts, and separate TestPyPI/production workflows. Adapt package names, paths, and Python versions. Publishing uses OIDC, attestations, and environment approval gates. Actual publishing is outside implementation acceptance.

## 3. Architecture and interfaces

### Components

| Component      | Responsibility                                                                 |
| -------------- | ------------------------------------------------------------------------------ |
| Settings       | Validate credentials, connection, workspace, indentation, and merge command    |
| Models         | Define notes, mailbox snapshots, state, operations, conflicts, and results     |
| IMAP transport | Connect, enumerate UIDs, fetch MIME, append, flag, and selectively expunge     |
| Text codec     | Decode MIME and HTML into canonical plain text; create plain-text MIME uploads |
| Storage        | Manage note files and atomically persist JSON state                            |
| Merge adapter  | Invoke the configured three-way merge tool                                     |
| Sync planner   | Compare base, local, and remote versions                                       |
| Sync executor  | Apply operations with checkpoints and restart recovery                         |
| CLI            | Expose commands, diagnostics, dry runs, and exit codes                         |

Use Pydantic `BaseModel` throughout domain and persisted data. Validate state on loading and saving; reject malformed or unsupported versions without resetting them.

Define an injectable transport interface implemented by the real `imaplib` adapter and an independent stateful mock server.

### Configuration

Use `BaseSettings` with the `APPLENOTES_` prefix.

| Setting                 | Default or requirement                       |
| ----------------------- | -------------------------------------------- |
| `IMAP_SERVER`           | Required for network operations              |
| `IMAP_USERNAME`         | Required for network operations              |
| `IMAP_PASSWORD`         | Required; represented by `SecretStr`         |
| `IMAP_FOLDER`           | `Notes`                                      |
| `IMAP_PORT`             | `993` for TLS                                |
| `IMAP_SECURITY`         | Verified TLS; also support required STARTTLS |
| `TIMEOUT_SECONDS`       | `30`                                         |
| `INDENT_SPACES`         | `4`                                          |
| `MERGE_COMMAND`         | Git command described below                  |
| `MERGE_TIMEOUT_SECONDS` | `30`                                         |

Load optional `.env` settings, then environment overrides, then explicit CLI overrides. Never persist credentials or expose them in diagnostics. Offline commands must work without credentials.

### Configurable merge tool

Use Git’s file-level merge as the default:

```json
["git", "merge-file", "--stdout", "--diff3", "{local}", "{base}", "{remote}"]
```

`git merge-file` provides Git merge behavior for three files without requiring a Git repository.

Expose configuration through `APPLENOTES_MERGE_COMMAND`, parsed as a JSON argument array, and a CLI `--merge-command` override using the same representation.

The external-tool contract is:

- Inputs are UTF-8 plain-text files identified by `{local}`, `{base}`, and `{remote}`.
- The tool writes the complete resulting text to stdout.
- Exit `0` means a clean merge; exit `1` means unresolved conflicts.
- Other exit codes, timeout, missing executable, or invalid UTF-8 mean an operational failure.
- The built-in Git adapter additionally recognizes Git’s documented positive conflict counts.
- Commands run without a shell. Reject missing or unknown placeholders before invocation.
- Temporary inputs live under `.applenotes` and are removed after execution.
- Tool failure preserves all original versions and does not upload anything.

External tools must support this contract directly or through a user-provided wrapper. Version one supports noninteractive tools only.

Return a Pydantic `MergeResult` containing outcome, resulting text, and sanitized diagnostics. Persist the merge command used for a conflict, but never credentials.

### Workspace and state

Store notes as `<sanitized-title>--<local-id>.txt`, with a permanent local ID independent of remote identifiers. The first line controls the remote title; filenames remain stable after creation.

Human-readable state contains:

- Schema version and account/mailbox identity.
- UIDVALIDITY, remote UID mappings, and identity evidence.
- Note paths, canonical base text, and content hashes.
- Conflicts, tombstones, pending operations, and recovery checkpoints.
- Last successful sync and structured errors.

Use atomic replacement and a transient workspace lock. Temporary files and locks reside under `.applenotes`; no separate durable database is introduced.

Reject workspace escapes, external symlinks, and accidental reuse with another account.

## 4. Text conversion and synchronization

### Plain text throughout

Parse MIME using Python’s email package and HTML using BeautifulSoup.

- Prefer HTML within `multipart/alternative`, then immediately convert it to text; otherwise use plain text.
- Process nested multipart bodies without duplicating alternatives.
- Ignore attachment parts, including text attachments.
- Preserve block boundaries, `<br>` breaks, blank lines, list text, and table cell text.
- Remove scripts, styles, comments, images, and embedded binary content.
- Decode entities and normalize nonbreaking indentation spaces.
- Normalize line endings to LF; preserve inline and trailing spaces.
- Convert only leading indentation into tabs.

The local file, merge inputs, merge result, and persisted merge bases contain plain text. Upload UTF-8 `text/plain` messages with actual tabs, the first-line subject, and Apple note-identification headers.

Downloading HTML does not itself replace the remote message. Any subsequent upload contains plain text only.

### Identity

ImapNotes3 can replace both the IMAP UID and note UUID when editing. Track identity using:

1. Known UID within unchanged UIDVALIDITY.
2. Unique matching Apple UUID.
3. Unique matching Message-ID.
4. Unique exact canonical content during recovery.

Never match solely by title or timestamp. Treat unmatched simultaneous disappearances and additions as identity ambiguity rather than automatically deleting notes.

Provide `link <file> --remote-uid <uid>` for explicit association.

A UIDVALIDITY reset triggers a full inventory while preserving local edits and merge bases. Missing previously synchronized mailboxes produce errors; only initialization with `--create-folder` creates one.

### Reconciliation

| Situation                               | Action                                       |
| --------------------------------------- | -------------------------------------------- |
| Neither side changed                    | No write                                     |
| Local changed                           | Upload replacement                           |
| Remote changed                          | Update local file                            |
| Both changed identically                | Advance base without upload                  |
| Both changed differently                | Invoke configured three-way merge            |
| Clean merge                             | Write locally and upload plain text          |
| Overlapping edits                       | Create two marked local copies; block upload |
| One side deleted, counterpart unchanged | Propagate deletion                           |
| One side deleted, counterpart edited    | Preserve content and record conflict         |
| Both deleted                            | Retain completed tombstone                   |
| Identity uncertain                      | Preserve versions and require linking        |

For unresolved text conflicts, write identical marked output to the tracked file and a `.remote-conflict.txt` companion. Preserve base and original inputs in state. Exclude companions from new-note discovery.

`resolve <file>` requires removal of conflict markers and rechecks the remote version. If it changed again, merge against that version. Remove companions only after successful resolution.

### Writes and recovery

Refresh relevant remote inventory and content before mutations. Persist operation intent before APPEND, verify the replacement, then retire the exact old UID.

Use APPENDUID when available; otherwise find the upload using its operation-specific Message-ID and verify content. Search before retrying uncertain APPEND outcomes.

Use UID-scoped deletion and selective expunge. Without selective expunge, mark only the target deleted and avoid mailbox-wide EXPUNGE.

Recheck local hashes before replacing files. Checkpoint completed operations so restart recovery avoids duplicate uploads and premature merge-base advancement.

IMAP cannot make the whole synchronization atomic across clients. Preserve observed versions and report ambiguous races.

### CLI

Provide `init`, `status`, `pull`, `push`, `sync`, `resolve`, and `link`.

Support `--json` for status and `--dry-run` for synchronization. Dry runs perform reads and planning without modifying notes, state, or remote messages.

Exit codes: `0` success, `1` operational failure, `2` invalid input/configuration, `3` unresolved conflicts or identity ambiguity.

## 5. Tests first and delivery

### Mock server

Implement and test an independent stateful mock IMAP server before production synchronization code. Model mailboxes, MIME bytes, monotonic UIDs, UIDVALIDITY, flags, capabilities, APPEND outcomes, and command history.

Support multiple clients, injected failures, lost responses, and scheduled concurrent edits. Add a loopback protocol fixture to test the real `imaplib` adapter.

### Required test cases

| Area                     | Cases                                                                                                                                                                                                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Mock server              | Authentication success/failure; mailbox creation/selection; UID allocation and searches; FETCH; APPENDUID present/absent; deleted flags; selective expunge; UIDVALIDITY reset; multiple clients; disconnects; committed APPEND with lost response                                    |
| Settings and models      | Required fields; defaults; `.env`/environment/CLI precedence; invalid settings; secret redaction; offline operation; JSON round trips; Unicode; invalid mappings/conflicts; unsupported schema; real environment isolation                                                           |
| Merge configuration      | Default Git argv; environment and CLI overrides; JSON parsing; placeholder validation; paths containing spaces; shell metacharacters treated literally; executable missing; timeout; tool failure; invalid UTF-8; Git conflict counts; custom-tool clean/conflicted output           |
| Storage                  | First/repeat initialization; corrupt/truncated state; account mismatch; atomic-write failure; interrupted replacement; lock contention/release; pending-operation reload; durable metadata confined to state                                                                         |
| MIME                     | HTML-only; plain-only; multipart alternatives; nested bodies; base64; quoted-printable; declared charsets; malformed MIME; missing headers; Unicode subjects; duplicate alternatives; stripping text/binary attachments                                                              |
| HTML conversion          | Adjacent blocks; paragraphs; `<br>` variants; empty blocks; blank lines; nested lists; tables; link labels; entities; nonbreaking spaces; scripts/styles/comments; images; malformed HTML; attachment-only notes                                                                     |
| Indentation              | Zero/multiple groups; remainder spaces; mixed tabs/spaces; configurable width; unchanged inline/trailing spaces; indented blank lines; CRLF; repeated normalization                                                                                                                  |
| Plain-text uploads       | `text/plain` content type; no generated HTML or attachments; Unicode; literal `<`/`&` preserved; blank lines; actual tabs; first-line title; empty-title fallback; Apple headers; UUID preservation; fresh Message-ID; canonical round trip                                          |
| Identity and paths       | Duplicate/unsafe/empty/Unicode titles; case collisions; stable filenames; missing/duplicate/changing UUIDs; UIDVALIDITY reset; exact-content recovery; ambiguous replacements; explicit linking; traversal and symlink rejection                                                     |
| Three-way merging        | Neither/either/both sides edited; identical changes; disjoint replacements; insertions/deletions; overlapping replacements; competing insertions; delete-versus-edit; adjacent hunks; Unicode; whitespace; empty files; final-newline differences; multiple conflicts                |
| Sync decisions           | Every reconciliation-table row; initial import; local/remote creation; title edits; multiple notes; empty mailbox; formatting-only remote changes; changed content with unchanged mtime; missing mailbox; deleted flags; ambiguous identity blocks deletion                          |
| Conflicts                | Two marked copies; originals retained; remote unchanged; companions ignored; repeated sync preserves edits; markers prevent resolution; successful resolution; further remote edits; deletion conflicts; cleanup after success only                                                  |
| IMAP adapter             | UID operations; BODY.PEEK; response parsing; APPENDUID/fallback lookup; Unicode mailboxes; empty results; NO/BAD responses; authentication failure; timeout; TLS/STARTTLS failure; fragmented literals; logout without expunge                                                       |
| Recovery and races       | Failure before APPEND; lost APPEND response; failure after verification; retirement failure; state-save failure; restart at each checkpoint; local edits during downloads; remote replacement during uploads; unrelated deleted messages preserved; retries do not duplicate uploads |
| CLI and packaging        | All commands; help/version; workspace discovery; initialization collision; JSON and exit codes; dry-run immutability; secret-free errors; invalid inputs; clean wheel/sdist installation; installed entry point                                                                      |
| Development and releases | Python 3.14 tox; hooks; lint/type/security checks; version consistency; RC/final normalization; changelog promotion; release-script guards; fixture exclusions                                                                                                                       |

### Implementation sequence

1. Bootstrap package metadata, tooling, documentation skeletons, and interface stubs.
2. Implement the mock server and complete failing behavior tests with real assertions.
3. Implement settings, models, state storage, and plain-text conversion.
4. Implement configurable merging, IMAP transport, reconciliation, and recovery.
5. Complete the CLI, contributor guide, changelog, and release tooling.
6. Run acceptance checks and verify the installed CLI in a clean environment.

```bash
uv sync --locked --group dev
uv run pre-commit install
uv run pre-commit run --all-files
uv run ty check src/applenotes
uv run tox -e py314
uv build
```

Require at least 90% overall branch coverage, with every listed merge, deletion, and recovery scenario passing regardless of the aggregate percentage.

Validate disposable plain-text notes on Apple clients: creation, editing, titles, deletions, actual tabs, and recognition of uploaded `text/plain` messages. Report any interoperability limitation without silently reverting to HTML.

Initial scope is one account and mailbox per workspace, explicit synchronization, and plain text. Background scheduling, OAuth, attachments, and CloudKit are deferred.
