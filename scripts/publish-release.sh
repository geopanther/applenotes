#!/usr/bin/env bash
# After PR is merged: checkout main, tag the current version, push tag.
#
# Usage: ./scripts/publish-release.sh
#
# Prerequisites:
#   - The version-bump PR has been merged to main

set -euo pipefail

DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"
git checkout "$DEFAULT_BRANCH"
git pull

VERSION="$(bump-my-version show current_version)"
TAG="v${VERSION}"

if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not determine current version" >&2
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: Tag ${TAG} already exists" >&2
    exit 1
fi

echo "==> Tagging ${TAG} and pushing to origin"
git tag "${TAG}"
git push origin "${TAG}"

echo "==> Done. Tag ${TAG} pushed."
if [[ "$VERSION" =~ -rc[0-9]+$ ]]; then
    echo "    This triggers deploy-test.yml → TestPyPI"
else
    echo "    This triggers deploy-prod.yml → PyPI"
fi
