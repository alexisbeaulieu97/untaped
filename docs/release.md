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
- Current release target: `4.0.0rc1`
- License metadata: `license = "MIT"` and `license-files = ["LICENSE"]`
- Build command: `uv build --no-sources`
- Public manifest: [`release-manifest.toml`](../release-manifest.toml), which
  records the package identity, Python floor, seven built-ins, direct
  requirements, and imported source OIDs.
- Unified smoke: install the wheel, invoke the executable `untaped`, require
  exact metadata and `untaped --version`, check the five management and seven
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

- `version`: release version without a leading `v`.
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

The v4 package is the release unit. The imported source OIDs and dependency
intersections are recorded in the public manifest; standalone source history
remains provenance for review.

## Adopting the release pipeline in a tool

Start from the two reusable templates in `.github/release/templates/`. Before committing the
tool copies, choose a reviewed, merged 40-character commit SHA from this repo that contains the
shared release checker version the tool should run. Substitute that same SHA for all three
`__CHECKER_SHA__` sites: the two `.release-tool` checkout refs in `release.yml.tmpl` and
`CORE_RELEASE_TOOL_SHA` in `test_release_workflow.py.tmpl`. A branch or tag is not an acceptable
substitute because the checker must remain immutable and reviewable.

Also replace the distribution and console-script sentinels and complete the test template's
`PER-TOOL CONFIG` block from the tool's `pyproject.toml`. After substitution, the workflow has no
template sentinels left, both checkout refs equal `CORE_RELEASE_TOOL_SHA`, and every action remains
pinned to a full commit SHA.

## TestPyPI Caveat

TestPyPI validates the release process and OIDC path, not reusable bytes.
Versions are immutable there too. If a TestPyPI upload burns a version, bump the
patch version and restart that package's release cycle.

For TestPyPI smokes, the workflow uses TestPyPI for the package under test and
PyPI for third-party dependencies via `UV_INDEX_STRATEGY=unsafe-best-match`.
Downstream tool smokes may still rely on production PyPI for already-published
upstream untaped packages during the release wave.

## Burn Recovery

Before publication, abort and restore the verified checkpoint in a fresh
checkout. Rehearse that restore and the draft/index boundaries with a local
fake transport before an authorized run.

After any public upload, files and tags are immutable. If smoke or GitHub
publication fails, reconcile the exact published state and fix forward under a
new approved version; never overwrite a filename, delete/reuse a tag, or retry
an ambiguous upload. Restore standalone operational paths from accepted source
bundles without destroying live checkouts, and preserve restricted config,
state, and expected skill manifests. Laptop cutover and later private
retirement checks are separate gates.

## Follow-Up

The external freeze receipt binds the final local candidate OID and artifact
hashes. It is separate from the public manifest and from any later runtime
attestation or cutover record.
