#!/usr/bin/env bash
# Create a release branch from the current (already bumped) version,
# sync lockfile, commit, push, open PR, watch CI checks, and squash-merge.
#
# Usage: ./scripts/merge-bump.sh
#
# Prerequisites:
#   - bump-my-version bump has already been run (without --commit)
#   - Working directory is the repo root
#   - gh CLI is authenticated

set -euo pipefail

trap 'echo "ERROR: Command failed at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

# Ensure we're on the default branch and in sync with remote
DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"
CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$DEFAULT_BRANCH" ]]; then
    echo "ERROR: Must be on '${DEFAULT_BRANCH}' branch (currently on '${CURRENT_BRANCH}'). Abort." >&2
    exit 1
fi

git fetch origin "$DEFAULT_BRANCH"
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/${DEFAULT_BRANCH}")"
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
    echo "ERROR: Local ${DEFAULT_BRANCH} (${LOCAL_SHA:0:8}) differs from remote (${REMOTE_SHA:0:8}). Pull or push first. Abort." >&2
    exit 1
fi

VERSION="$(bump-my-version show current_version)"
if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not determine current version" >&2
    exit 1
fi

# Detect if this is an RC version
if [[ "$VERSION" =~ -rc[0-9]+$ ]]; then
    BRANCH="chore/bump-${VERSION}"
    PR_TITLE="Bumping version to ${VERSION}"
    # Revert RC heading in CHANGELOG.md — keep only ## Unreleased
    if ! python scripts/revert_changelog_rc.py; then
        echo "WARNING: revert_changelog_rc.py failed" >&2
    fi
else
    BRANCH="chore/release-${VERSION}"
    PR_TITLE="Release ${VERSION}"
fi

echo "==> Preparing release for v${VERSION} on branch ${BRANCH}"

# Create branch
git checkout -b "${BRANCH}"

# Sync lockfile
UV_LOCKED=0 uv sync --all-groups

# Commit only bumped files + lockfile
git add pyproject.toml src/appletextnotes/__init__.py .bumpversion.toml CHANGELOG.md uv.lock
git commit --no-edit -m "Bump version: ${VERSION}"

# Push and create PR
git push --set-upstream origin "${BRANCH}"
gh pr create --title "${PR_TITLE}" --body "${PR_TITLE}"

# Wait for CI checks to register (max 30s)
echo "==> Waiting for CI checks to be registered..."
for i in $(seq 1 30); do
    sleep 1
    gh pr checks && RC=$? || RC=$?
    if [[ $RC -eq 0 || $RC -eq 8 ]]; then
        break
    fi
done

if [[ $RC -ne 0 && $RC -ne 8 ]]; then
    echo "ERROR: No CI checks appeared after 30s. Abort." >&2
    exit 1
fi

# Watch checks until complete, then merge on success
echo "==> Watching CI checks..."
gh pr checks --watch --interval 1 --fail-fast
echo "==> Merging PR..."
gh pr merge --squash --delete-branch
