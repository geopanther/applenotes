"""Pure reconciliation decisions; absence is distinct from an empty note."""

from typing import Literal

Action = Literal[
    "none",
    "upload",
    "download",
    "advance",
    "merge",
    "delete_local",
    "delete_remote",
    "tombstone",
    "conflict",
]


def decide(base: str, local: str | None, remote: str | None) -> Action:
    if local is None and remote is None:
        return "tombstone"
    if local is None:
        return "delete_remote" if remote == base else "conflict"
    if remote is None:
        return "delete_local" if local == base else "conflict"
    if local == remote:
        return "none" if local == base else "advance"
    if local == base:
        return "download"
    if remote == base:
        return "upload"
    return "merge"
