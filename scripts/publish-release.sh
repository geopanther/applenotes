#!/usr/bin/env bash
# Default is a readiness check; --push explicitly creates and pushes a tag.
set -euo pipefail
exec uv run --locked python scripts/release.py tag "$@"
