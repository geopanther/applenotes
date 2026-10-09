# Releasing

This document describes how to release new versions of applenotes.

## Overview

The project uses a two-stage release pipeline:

1. **Release candidate** → published to [TestPyPI](https://test.pypi.org/project/applenotes/) for validation (with approval gate)
2. **Final release** → published to [PyPI](https://pypi.org/project/applenotes/) (with approval gate)

All publishing uses [PyPI Trusted Publishers (OIDC)](https://docs.pypi.org/trusted-publishers/) — no API tokens are involved. Packages include [digital attestations](https://docs.pypi.org/attestations/) for provenance verification.

## Prerequisites

- Write access to the repository
- For RC releases: approval rights on the `pypi-publish-test` GitHub environment
- For production releases: approval rights on the `pypi-publish-prod` GitHub environment
- Local dev environment set up (see [CONTRIBUTING.md](../CONTRIBUTING.md))
- On the default branch, in sync with remote (enforced by `merge-bump.sh`).

## Version format

Versions follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH` with optional `-rcN` suffix for release candidates.

Version bumping is managed by [bump-my-version](https://github.com/callowayproject/bump-my-version) via `.bumpversion.toml`. It updates `pyproject.toml`, `src/applenotes/__init__.py`, and `CHANGELOG.md` automatically.

Two helper scripts handle the git workflow after bumping:

- `scripts/merge-bump.sh` — creates branch, syncs lockfile, commits, pushes, opens PR, watches CI, and squash-merges
- `scripts/publish-release.sh` — checks out main, tags the release, pushes tag to trigger deployment

## Preparation

To avoid having to prepend all commands with `uv run`, simply activate the current environment.

```bash
. .venv/bin/activate
```

Ensure that your local `.venv` is in sync.

```bash
uv sync --locked
```

## Release candidate

Use this to test a release on TestPyPI before publishing to production.

### 1. Bump version

From the up-to-date `main` branch, bump to the desired RC version (`0.2.1` → `0.3.0-rc0`):

```bash
bump-my-version bump minor
```

For subsequent release candidates (`rc0` → `rc1`, `rc1` → `rc2`, etc.):

```bash
bump-my-version bump pre_n
```

Verify the result:

```bash
bump-my-version show current_version
```

### 2. Prepare and merge

```bash
./scripts/merge-bump.sh
```

This creates a branch, syncs the lockfile, commits, pushes, opens a PR, watches CI
and squash-merges it. For RC versions, it removes the RC heading from `CHANGELOG.md` (keeping only `## Unreleased`).

### 3. Tag and push

```bash
./scripts/publish-release.sh
```

This checks out `main`, tags `v<version>`, and pushes. Triggers `deploy-test.yml`: **build → publish to TestPyPI**.

### 4. Approve the TestPyPI publish

Go to the Actions tab, find the running workflow, and approve the `publish-testpypi` job when prompted by the `pypi-publish-test` environment gate. Wait for the publish job to complete successfully before proceeding.

### 5. Verify on TestPyPI

Check the package page at `https://test.pypi.org/project/applenotes/<VERSION>/` and confirm:

- The package is listed
- Attestations are present (visible under "Provenance")

To test installation, install the CLI in an isolated environment with Python 3.14.
Add TestPyPI as an index and explicitly keep PyPI as the default so dependencies
such as `pydantic-settings` can be resolved. Using TestPyPI as the default index
alone excludes PyPI and can fail on a clean machine.
Use `--refresh-package applenotes` to fetch fresh index data so uv can discover
the newly published version.

```bash
uv tool install --python 3.14 \
  --index https://test.pypi.org/simple/ \
  --default-index https://pypi.org/simple/ \
  --index-strategy unsafe-best-match \
  --refresh-package applenotes \
  "applenotes==<VERSION>"
applenotes --help
```

### 6. Iterate if needed

Repeat steps 1–5 using `bump-my-version bump pre_n` to increment the RC number.

## Final release

### 1. Bump version

From the up-to-date `main` branch, bump to the final version (e.g. `0.3.0-rc1` → `0.3.0`):

```bash
bump-my-version bump pre_l
```

Verify:

```bash
bump-my-version show current_version
```

### 2. Prepare and merge

```bash
./scripts/merge-bump.sh
```

### 3. Tag and push

```bash
./scripts/publish-release.sh
```

This triggers `deploy-prod.yml`: **build → GitHub Release → PyPI**.

The GitHub Release is created automatically with notes extracted from `CHANGELOG.md`. The `pypi-publish-prod` environment requires manual approval before publishing to PyPI.

### 4. Approve the production publish

Go to the Actions tab, find the running workflow, and approve the `publish-pypi` job when prompted by the `pypi-publish-prod` environment gate. Wait for the publish job to complete successfully before proceeding.

### 5. Verify on PyPI

Check the package page at `https://pypi.org/project/applenotes/<VERSION>/` and confirm:

- The package is listed
- Attestations are present (visible under "Provenance")

To test installation from PyPI:

```bash
uv tool install --python 3.14 \
  --default-index https://pypi.org/simple/ \
  --refresh-package applenotes \
  "applenotes==<VERSION>"
applenotes --help
```

## Workflows

| Workflow          | Trigger                 | Pipeline               | Environment         |
| ----------------- | ----------------------- | ---------------------- | ------------------- |
| `deploy-test.yml` | `v*-rc*` tags (RC only) | build → TestPyPI       | `pypi-publish-test` |
| `deploy-prod.yml` | `vX.Y.Z` tags (no rc)   | build → release → PyPI | `pypi-publish-prod` |

## Security

- All GitHub Actions are pinned to full commit SHAs (not tags)
- Build and publish run in separate jobs — publish jobs never have access to source code or build tools
- OIDC authentication means no long-lived secrets exist
- Digital attestations provide cryptographic proof linking each package to this repository
- Both `pypi-publish-test` and `pypi-publish-prod` environments require reviewer approval
