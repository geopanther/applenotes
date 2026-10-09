# applenotes

Synchronize one IMAP Notes mailbox with editable UTF-8 plain-text files. Python
3.14 and Git (for the default merge command) are required. macOS and Linux are
supported.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install
applenotes:

```bash
uv tool install applenotes
applenotes --help
```

Set connection settings in your environment or an optional `.env` in the current
working directory. Explicit command-line settings take precedence over environment
variables, which take precedence over `.env`. Passwords have no CLI argument.
If `APPLENOTES_IMAP_PASSWORD` is unset, network commands prompt for it with input
hidden. The entered password is used only for that invocation and is never saved.
Set the variable for unattended commands. An explicitly empty password is invalid.

```bash
export APPLENOTES_IMAP_SERVER=imap.example.org
export APPLENOTES_IMAP_USERNAME=you@example.org
# Optionally set APPLENOTES_IMAP_PASSWORD to skip the password prompt.
mkdir notes
cd notes
applenotes init
applenotes sync --dry-run
applenotes sync
applenotes status --json
```

`init --create-folder` explicitly permits creating a missing mailbox. Subsequent
commands never recreate a missing mailbox. The default mailbox is `Notes`.
`--workspace PATH` selects a workspace; other commands also search parent
directories for an initialized workspace. Global options precede the command.
`status` is offline and does not read credentials.

- `pull` downloads remote changes and propagates unchanged remote deletions locally.
- `push` uploads local changes and propagates unchanged local deletions remotely.
- `sync` reconciles both directions, including three-way merges.
- `--dry-run` on these commands reads and plans without writing local or remote data.
- `resolve FILE` uploads an edited conflict file after checking the latest remote version.
- `link FILE --remote-uid UID` explicitly associates a tracked file with a remote note.

Directional commands defer merges requiring writes to both sides; use `sync` for
those changes. JSON plans name each action. Exit codes are `0` for success, `1`
for operational errors, `2` for invalid configuration/input, and `3` for conflicts
or uncertain identity. `status --json` reports persisted state, including pending
operations and original conflict versions. Text status also compares local files.

Each note's first line is its title. Imported names have a permanent local ID:
`Title--id.txt`. Title edits do not rename files. Create new notes as `.txt` files
in the workspace root. Files are compared by content, independently of mtimes.
Deletion is a synchronization action: use a dry run to review it.

All incoming HTML becomes plain text before comparison, storage, or merging.
Formatting, images, attachments (including text attachments), scripts and styles
are discarded. HTML alternatives take precedence over plain alternatives. Block
breaks, list labels and table cells remain readable. Outgoing messages are always
UTF-8 `text/plain`, with Apple note identification headers. Importing HTML alone
does not replace the remote message.

Complete groups of four leading spaces become actual tabs; remainder, inline and
trailing spaces remain. LF line endings and final-newline differences are preserved.
Set `APPLENOTES_INDENT_SPACES` or `--indent-spaces` to change the width before
initializing a workspace; changing it later can appear as content edits.

| Setting (`APPLENOTES_` prefix) | Default                                              |
| ------------------------------ | ---------------------------------------------------- |
| `IMAP_SERVER`, `IMAP_USERNAME` | Required for network commands                        |
| `IMAP_PASSWORD`                | Prompted if unset                                    |
| `IMAP_FOLDER`                  | `Notes`                                              |
| `IMAP_SECURITY`                | `tls`; alternatively `starttls` (required, verified) |
| `IMAP_PORT`                    | 993 for TLS; 143 for STARTTLS                        |
| `TIMEOUT_SECONDS`              | 30                                                   |
| `INDENT_SPACES`                | 4                                                    |
| `MERGE_TIMEOUT_SECONDS`        | 30                                                   |
| `MERGE_COMMAND`                | JSON argv shown below                                |

The default merge command works outside a Git repository:

```json
["git", "merge-file", "--stdout", "--diff3", "{local}", "{base}", "{remote}"]
```

Override it using `APPLENOTES_MERGE_COMMAND` or `--merge-command` with a JSON
argument array. All three placeholders are required. No shell is involved; shell
metacharacters are literal. Literal braces in other arguments must be doubled.
Inputs are UTF-8 files in a temporary `.applenotes` directory. The tool must output
the complete merged text on stdout: exit 0 means clean, exit 1 means conflicted.
The built-in Git adapter also accepts Git's positive conflict counts up to 127.
Timeouts, invalid UTF-8, missing executables and other exit codes stop the operation
without uploading. Interactive tools require a noninteractive wrapper.

Unresolved merges create identical marked copies in the tracked file and a
`.remote-conflict.txt` companion. The remote stays unchanged. Edit the tracked
file, remove all conflict markers, then run `resolve FILE`. Further remote edits
are merged against the remote version observed at conflict creation. Companions
are excluded from note discovery and removed after successful resolution.
Edit-versus-delete conflicts also preserve both observations. Saving a resolved
file and running `resolve` chooses to retain that note.

Identity uses known UIDs within UIDVALIDITY, then unique Apple UUIDs, Message-IDs,
and exact canonical content. Titles and timestamps are never identity evidence.
Clients that replace both UUID and content can require explicit linking; ambiguous
remote additions are held for review rather than causing local deletions. Use a
read-only IMAP client to inspect UIDs when linking.

Durable metadata lives solely in `.applenotes/state.json`, including merge bases,
identity evidence, conflicts, completed tombstones, operation intents and recovery
checkpoints. Atomic replacements and a transient directory lock protect writes.
Symlinks and workspace escapes are rejected. Credentials are not stored in state.
The state file **does contain note content**; protect and back it up with your files.
Do not reuse a workspace with a different account, mailbox or transport identity.

APPEND intent is saved before transmission. Uploads use an operation-specific
Message-ID and verify content before retiring the old UID. Restart searches before
retrying an uncertain APPEND, preventing duplicate uploads. Only the exact old UID
is flagged deleted. Selective expunge requires UIDPLUS; other servers keep that
message flagged until another client expunges it. Mailbox-wide EXPUNGE and CLOSE
are never issued. IMAP cannot provide a cross-client transaction: ambiguous races
stop and retain observations in pending state. Back up state before manually
repairing pending operations; do not delete state to bypass a recovery error.

See [contributing](CONTRIBUTING.md), [release procedures](docs/releasing.md), and
[validation and Apple interoperability](docs/validation.md). Background scheduling,
OAuth, attachment synchronization and CloudKit are outside this version's scope.

The development/release conventions are adapted from
[mdfluence](https://github.com/geopanther/mdfluence). Apple note headers and
replacement identity behavior were inspected in
[ImapNotes3](https://github.com/niendo1/ImapNotes3).
