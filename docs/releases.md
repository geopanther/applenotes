# Releases

Versions follow Semantic Versioning, with candidates written `0.2.0-rc1` in source
and normalized to `0.2.0rc1` in wheel metadata. `bump-my-version` keeps
`pyproject.toml`, `src/applenotes/__init__.py` and `.bumpversion.toml` consistent.
Final releases promote Unreleased changes to a dated changelog section. Candidate
headings are removed so those entries stay under Unreleased until the final release.

From a clean `main` checkout:

```bash
./scripts/merge-bump.sh 0.2.0-rc1
uv run tox -e py314
uv run pre-commit run --all-files
uv build
```

The helper creates a local release branch, bumps version files, repairs candidate
changelog headings and refreshes `uv.lock`. Review the changes, commit them and
open a pull request. Merge only after CI succeeds. The helper does not automatically
push or merge anything. Preparing `0.2.0` after a candidate creates the final dated
changelog entry using the same process.

After the PR is merged, update a clean local `main` and check readiness:

```bash
./scripts/publish-release.sh
```

The script rejects version mismatches, missing final changelog entries, dirty
checkouts, non-main branches and existing tags. To actually create and push the tag:

```bash
./scripts/publish-release.sh --push
```

That command fetches `origin/main` and requires an exact match with local HEAD.
Pushing candidate tags triggers `deploy-test.yml` for TestPyPI; final tags trigger
`deploy-prod.yml` for PyPI. Both workflows recheck tag/version consistency, run
Python 3.14 tests and build artifacts before publishing.

Repository administrators must configure Trusted Publishing for each registry and
create `pypi-publish-test` and `pypi-publish-prod` GitHub environments with required
reviewers. Approval gates are repository environment settings; YAML naming alone
cannot configure them. Both publishing jobs use OIDC, attestations, pinned actions
and no stored API token. A production approval must explicitly authorize publishing
the reviewed distribution. Actual publishing is not an implementation check.
