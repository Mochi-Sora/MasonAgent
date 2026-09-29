# Release checklist

Use this checklist with the [Release Packaging Contract](../CONTRIBUTING.md#release-packaging-contract).
Preparing a release does not publish it. Pushing a Git tag, publishing a GitHub Release, and
uploading to PyPI are separate operations.

## Prepare a candidate

1. Choose the previous release and the exact candidate commit. Work in a clean worktree;
   do not include local configuration, session data, credentials, or unrelated changes.
2. Update `project.version` in `pyproject.toml` and the final source-only fallback in
   `nanobot/__init__.py`. Private package versions are not the Python release version.
3. Review the changes since the previous tag. Write highlights, upgrade and rollback guidance,
   contributor acknowledgements, and the full changelog. Verify counts against the final
   range; commit counts are not merged-PR counts. Coordinate security disclosures separately.
4. Run the CI checks and confirm the final commit's CI status, not just an earlier PR head.
   At minimum:

   ```bash
   uv sync --all-extras --dev
   uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
   uv run --no-sync ruff check nanobot tests conftest.py
   uv run --no-sync basedpyright
   uv run --no-sync pytest
   ```

   Review installation, configuration, session, and API changes.
5. Build in a clean output directory with `uv build --out-dir <artifact-directory>`.
   The wheel is built from the source distribution.
6. Check the distributions with `twine check`, inspect their contents, and record SHA-256
   hashes. Test installation in an isolated environment outside the source checkout, then
   test upgrading from the previous stable version using disposable configuration and sessions.
   Do not use a maintainer's live workspace for migration tests.
7. Merge the release-preparation PR only after its current checks and reviews pass. Confirm the
   merged source tree matches the tested candidate; if it does not, rebuild and recheck before
   tagging. Reconcile the final changelog. The tag must point to this verified commit, not an
   unchecked later `main` tip.

Keep an artifact manifest with the source commit, version, filenames, hashes, checks performed,
and any remaining release gates. Rebuild and recheck if the packaged source changes.

## Publish, with maintainer approval

1. Confirm all pre-tag gates above are complete. Create and push exactly `vX.Y.Z`; do not move
   or reuse a published version tag.
2. Create the matching GitHub Release. A draft may be used while assembling its attachments.
3. Upload the checked `nanobot_ai-X.Y.Z.tar.gz` and `nanobot_ai-X.Y.Z-py3-none-any.whl` to PyPI.
   Do not upload stale files from a shared `dist/` directory. This repository has no automatic
   PyPI publication workflow.
4. Verify installation from PyPI in a clean environment, including `nanobot onboard` and one
   real `nanobot agent -m "Hello!"` reply.
5. Publish the release announcement and add the dated entry to
   [Release Archive](./release-archive.md). Complete any coordinated advisory publication and
   reporter notification. Record links and completion status in the release tracking issue.

## If a gate fails

Do not publish to PyPI while a required check is failing. If a published package needs a
correction, prepare a new version; do not silently replace the code behind an existing release
tag. Stop all old processes and follow the documented session rollback procedure before
downgrading a migrated installation.
