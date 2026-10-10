# Validation and interoperability

The acceptance suite uses synthetic content and an independent mock IMAP server.
No real credentials or existing environment files are needed. The protocol fixture
uses a localhost socket to exercise the real imaplib parser with fragmented MIME
literals. Mock clients share mailboxes, UIDs and flags; scheduled edits and failures
cover races and committed APPENDs with lost responses.

Coverage spans MIME conversion, indentation, settings precedence, validated state,
atomic IO, merges, all reconciliation decisions, conflict copies, identity matching,
UIDVALIDITY resets, directional commands, dry runs, selective expunge, operation
checkpoints, release guards and installed package behavior. Run:

```bash
uv run tox -e py314
uv build
```

For a manual wheel smoke test, install the wheel in a fresh Python 3.14 environment,
run `appletextnotes --version` and `appletextnotes --help`, then run an offline
`appletextnotes status --json` against a synthetic workspace. Inspect the wheel and
sdist: neither should contain `.env`, workspace state, credentials or `.venv`.

## Recorded implementation checks

On 2026-10-08 with Python 3.14.7, the isolated suite passed all 202 cases with
96.10% combined coverage and 91.57% branch coverage. Wheel and sdist installations
in separate fresh environments passed CLI help, version and offline JSON status.
Ruff, ty and Bandit checks passed. The OSV scan identified the Gitlint wrapper's
legacy `sh` pin; development tooling now uses `gitlint-core` and patched `sh`.

## Apple client acceptance

Live Apple client recognition of `text/plain` notes remains an external acceptance
check. Automated MIME/header tests do not establish that a particular macOS/iOS
version recognizes these messages. Do not silently switch uploads to HTML if a
client fails this check.

Use a disposable account or a dedicated test mailbox and an isolated workspace;
keep credentials outside any test artifacts. Record the server and macOS/iOS
versions with these results:

1. Create a plain-text note locally with a Unicode title and sync it. Verify the
   Apple client recognizes it as a note with the expected title and body.
2. Include actual tabs, remainder indentation spaces, inline `<`/`&`, blank lines,
   trailing spaces and a final line without a newline. Compare after a round trip.
3. Edit it on the Apple client, pull, edit locally and push. Verify title edits and
   confirm the upload remains `text/plain` without attachments or HTML.
4. Make disjoint edits on both clients and sync; verify the combined text.
5. Make overlapping edits, verify two marked local files and unchanged remote
   content, resolve the tracked file and verify companion cleanup.
6. Delete unchanged notes on each side and verify propagation. Exercise an
   edit-versus-delete conflict and confirm the edited text survives.
7. Test a server without UIDPLUS and confirm unrelated deleted messages survive.

A failed recognition or whitespace result is an interoperability limitation to
record here, with client/server versions. Real Apple validation and registry
publishing are not performed by unit tests or the release-preparation helpers.
