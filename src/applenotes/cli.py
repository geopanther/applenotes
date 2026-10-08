"""Command-line interface. Offline diagnostics never require credentials."""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from applenotes import __version__
from applenotes.engine import SyncEngine, SyncError
from applenotes.settings import Settings
from applenotes.storage import Storage, StorageError
from applenotes.transport import IMAPTransport, TransportError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="applenotes", description="Synchronize IMAP notes as plain text."
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--workspace", type=Path, default=Path.cwd())
    root.add_argument("--env-file", type=Path, help="Optional dotenv file (default: .env).")
    root.add_argument("--imap-server")
    root.add_argument("--imap-username")
    root.add_argument("--imap-folder")
    root.add_argument("--imap-port", type=int)
    root.add_argument("--imap-security", choices=["tls", "starttls"])
    root.add_argument("--indent-spaces", type=int)
    root.add_argument("--merge-command", help="JSON argv array with {local}, {base}, {remote}.")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize a workspace for the configured mailbox.")
    init.add_argument("--create-folder", action="store_true")
    status = commands.add_parser(
        "status", help="Read local synchronization state without connecting."
    )
    status.add_argument("--json", action="store_true")
    for name in ["pull", "push", "sync"]:
        command = commands.add_parser(name)
        command.add_argument("--dry-run", action="store_true")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("file")
    link = commands.add_parser("link")
    link.add_argument("file")
    link.add_argument("--remote-uid", type=int, required=True)
    return root


def discover(root: Path) -> Path:
    for path in [root, *root.parents]:
        if (path / ".applenotes" / "state.json").exists():
            return path
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    transport = None
    try:
        root = args.workspace if args.command == "init" else discover(args.workspace)
        store = Storage(root)
        if args.command == "status":
            state = store.load()
            if args.json:
                print(state.model_dump_json(indent=2))
            else:
                print(
                    f"{len(state.notes)} notes, "
                    f"{sum(n.conflict is not None for n in state.notes.values())} conflicts, "
                    f"{len(state.pending)} pending operations"
                )
                for note in state.notes.values():
                    local = store.read(note.path)
                    label = (
                        "conflict"
                        if note.conflict
                        else "deleted"
                        if note.deleted
                        else "missing"
                        if local is None
                        else "modified"
                        if local != note.base_text
                        else "unchanged"
                    )
                    print(f"{note.path}: {label}")
            return 3 if any(n.conflict for n in state.notes.values()) else 0
        values = {
            key: value
            for key, value in vars(args).items()
            if key in Settings.model_fields and value is not None
        }
        if args.env_file is not None:
            values["_env_file"] = args.env_file
        try:
            settings = Settings(**values)
            settings.require_network()
        except ValidationError, ValueError, SettingsError:
            print(
                "Invalid configuration; check APPLENOTES_ settings and merge-command JSON.",
                file=sys.stderr,
            )
            return 2
        transport = IMAPTransport.connect(settings)
        engine = SyncEngine(store, transport, settings)
        if args.command == "init":
            engine.initialize(create_folder=args.create_folder)
            print(f"Initialized {root}")
        elif args.command in {"sync", "pull", "push"}:
            result = engine.sync(mode=args.command, dry_run=args.dry_run)
            print(result.model_dump_json(indent=2))
            return 3 if result.conflicts else 0
        elif args.command == "link":
            engine.link(args.file, args.remote_uid)
        else:
            engine.resolve(args.file)
            if any(n.conflict for n in store.load().notes.values()):
                return 3
        return 0
    except SyncError as error:
        print(str(error), file=sys.stderr)
        return 2 if error.invalid else 1
    except StorageError, TransportError, OSError, UnicodeError:
        print("Operation failed; check workspace state and connection settings.", file=sys.stderr)
        return 1
    finally:
        if transport is not None:
            transport.close()
