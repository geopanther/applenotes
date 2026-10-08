#!/usr/bin/env bash
# Prepare reviewable release files locally. Never pushes or merges a PR.
set -euo pipefail
exec uv run --locked python scripts/release.py prepare "$@"
