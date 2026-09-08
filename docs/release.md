# Releasing `untaped` to PyPI/TestPyPI

The unified `untaped` application releases through
`.github/workflows/release.yml`. The workflow is manual-only, builds one wheel
and one source archive, uses PyPI Trusted Publishing, smoke-installs the
published package from the selected index, and publishes an exact GitHub draft
only after the production smoke succeeds.

Do not publish, dispatch release workflows, create tags/releases, merge PRs, or
change repository settings without explicit approval for that exact action.

## Package Metadata

- Package name: `untaped`
- Current release target: `5.0.0`
- License metadata: `license = "MIT"` and `license-files = ["LICENSE"]`
- Build command: `uv build --no-sources`
- Public manifest: [`release-manifest.toml`](../release-manifest.toml), which
  records the package identity, Python floor, six built-ins, direct
  requirements, and imported source OIDs.
- Unified smoke: install the wheel, invoke the executable `untaped`, require
  exact metadata and `untaped --version`, check the five management and six
  capability roots in root help, then resolve every capability's `--help`
  command offline. Local and published jobs call the same
  `.github/release/release.py smoke-unified` implementation.

## Trusted Publishers

Create pending publishers on both TestPyPI and PyPI before dispatching the
workflow:

- Owner: `alexisbeaulieu97`
- Repository: `untaped`
- Workflow: `.github/workflows/release.yml`
- Package: `untaped`
- Environment: `testpypi` for TestPyPI, `pypi` for PyPI

Create matching GitHub environments:

- `testpypi`: exists before the TestPyPI dispatch.
- `pypi`: requires reviewer approval.

Repository settings and environment changes are out-of-band operations. Make
them deliberately, and record what changed in the release PR or release notes.

## Workflow Dispatch

Inputs:

- `version`: release version without a leading `v`, matching `X.Y.Z` with an
  optional `aN`, `bN`, or `rcN` suffix. Bare `X.Y.Z` creates a stable GitHub
  draft; the suffixed forms create prerelease drafts.
- `candidate_oid`: full 40-character reviewed commit SHA. It must equal the
  checkout's `GITHUB_SHA`.
- `index`: `testpypi` or `pypi`.

Rules:

- For the first release-workflow introduction, merge the reviewed PR before
  dispatching TestPyPI because GitHub only accepts `workflow_dispatch` events
  when the workflow file exists on the default branch.
- After `release.yml` exists on `main`, later TestPyPI rehearsals may target a
  reviewed release branch via the dispatch `ref`.
- Production PyPI must run from `refs/heads/main`; the workflow fails otherwise.
- Candidate identity and the package manifest are checked before any remote
  mutation. Build/test/local smoke run in a read-only job.
- A production run creates or resumes a GitHub draft targeted at the exact
  candidate and containing exactly the wheel and source archive. Existing
  drafts and assets are accepted only after their tag, target, and SHA-256
  values match.
- The publish job only downloads the built distributions and calls
  `pypa/gh-action-pypi-publish`; it has `id-token: write` but no write access
  to repository contents.
- The published-package job verifies the exact index filename-to-SHA-256 set
  and runs the shared unified smoke in a read-only job.
- The final GitHub job runs only for `index = pypi` after the published smoke
  passes, and publishes the already validated draft. It never creates a
  replacement release or overwrites an asset.
- Action refs are pinned to full commit SHAs.
- `pypa/gh-action-pypi-publish` performs the upload; do not use `uv publish`
  for this workflow because the PyPA action emits provenance attestations under
  Trusted Publishing.

## Restartable state machine

Production publication is an ordered prefix:

1. validate the reviewed candidate, package metadata, wheel, source archive,
   and local smoke;
2. create or inspect the exact GitHub draft and upload only missing exact
   assets;
3. inspect the selected index for the exact immutable filename/hash set and
   upload through Trusted Publishing when absent;
4. install and smoke the published package;
5. publish the matching GitHub draft.

Retries inspect every completed prefix before continuing. Missing, conflicting,
ambiguous, or unverifiable remote state fails closed. A fully matching
published release is a verified no-op after the published smoke. TestPyPI
rehearsals omit the GitHub draft/publish states but retain immutable-file and
smoke verification.

## Release Order

The `untaped` package is the release unit. Its supported Python floor,
capability order, direct requirements, and reviewed source inputs are recorded
in [`release-manifest.toml`](../release-manifest.toml).

## TestPyPI Caveat

TestPyPI validates the release process and OIDC path, not reusable bytes.
Versions are immutable there too. If a TestPyPI upload burns a version, bump the
patch version and restart that package's release cycle.

For TestPyPI smokes, the workflow uses TestPyPI for the package under test and
PyPI for third-party dependencies via `UV_INDEX_STRATEGY=unsafe-best-match`.

## Burn Recovery

Use the following procedure when a release run needs to be resumed. Replace
the placeholders with the externally recorded checkpoint bundle and reviewed
candidate; do not use a working checkout as the recovery source.

```bash
CHECKPOINT_BUNDLE=/secure/path/to/untaped-checkpoint.bundle
CHECKPOINT_OID=<40-character-reviewed-checkpoint-oid>
RESTORE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/untaped-release-restore.XXXXXX")"

git clone --no-hardlinks "$CHECKPOINT_BUNDLE" "$RESTORE_DIR"
git -C "$RESTORE_DIR" checkout --detach "$CHECKPOINT_OID"
test "$(git -C "$RESTORE_DIR" rev-parse HEAD)" = "$CHECKPOINT_OID"
test -z "$(git -C "$RESTORE_DIR" status --porcelain)"
git -C "$RESTORE_DIR" fsck --full --strict
```

Before an authorized run, run the focused release tests from that clean
checkout. The fake transport covers draft creation, partial asset/index
prefixes, smoke failure, publication failure, exact completed no-op, and
conflicts; it performs no network or publish operation:

```bash
cd "$RESTORE_DIR"
UV_CACHE_DIR=/path/to/writable/uv-cache \
  uv run --locked pytest -o addopts='' \
  .github/release/tests/test_release_helper.py \
  tests/unit/test_release_workflow.py \
  tests/unit/test_release_smoke_workflow.py
```

After an interrupted run, inspect the exact public prefix before choosing the
next transition. Resume only when every observed identity is provable:

- an existing release has tag `v<version>`, its target and resolved tag point
  to the reviewed candidate, and every visible asset has the expected SHA-256;
- an index contains only the candidate's exact wheel and source archive for
  that version, with matching SHA-256 values; and
- a draft may be missing a tag until publication, while a published release
  must have a resolved matching tag.

An exact draft receives only its missing assets. An exact partial index
receives only its missing files. An exact published release is a verified
no-op after the published smoke. A timeout, non-404 response, conflicting
hash, unexpected file, target mismatch, or unresolved published tag stops the
run until the state is reconciled; never retry an ambiguous upload.

Use a new approved version only after reconciliation proves that an immutable
public filename or tag contains bytes or identity for another candidate, or
when the original candidate can no longer be proven. Never overwrite a
filename, delete/reuse a tag, or silently switch candidate OIDs. Restore
required operational paths from accepted source bundles without destroying live
checkouts, and preserve restricted config, state, and expected skill manifests.
