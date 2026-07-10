# Releasing `untaped` to PyPI/TestPyPI

The SDK releases through `.github/workflows/release.yml`. The workflow is
manual-only, publishes with PyPI Trusted Publishing, smoke-installs the
published package from the selected index, and creates the GitHub release/tag
only after a production PyPI smoke succeeds.

Do not publish, dispatch release workflows, create tags/releases, merge PRs, or
change repository settings without explicit approval for that exact action.

## Package Metadata

- Package name: `untaped`
- Current release target: `3.1.0`
- License metadata: `license = "MIT"` and `license-files = ["LICENSE"]`
- Build command: `uv build --no-sources`
- SDK smoke: install the wheel, import `untaped.api`, and assert no `untaped`
  console script exists.
- Tool-package smoke: when a package declares a console script, invoke its real
  installed `<script> --version` and require stdout to equal the distribution
  version plus one trailing newline before checking `--help`.

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
- `index`: `testpypi` or `pypi`.

Rules:

- For the first release-workflow introduction, merge the reviewed PR before
  dispatching TestPyPI because GitHub only accepts `workflow_dispatch` events
  when the workflow file exists on the default branch.
- After `release.yml` exists on `main`, later TestPyPI rehearsals may target a
  reviewed release branch via the dispatch `ref`.
- Production PyPI must run from `refs/heads/main`; the workflow fails otherwise.
- Build/test/smoke runs in a read-only build job.
- The publish job only downloads the built distributions and calls
  `pypa/gh-action-pypi-publish`; it has `id-token: write` but no write access
  to repository contents.
- The published-package smoke runs after upload in a read-only job.
- The GitHub release/tag job runs only for `index = pypi` after the published
  smoke passes, and it is the only job with `contents: write`.
- Action refs are pinned to full commit SHAs.
- `pypa/gh-action-pypi-publish` performs the upload; do not use `uv publish`
  for this workflow because the PyPA action emits provenance attestations under
  Trusted Publishing.

## Release Order

For the repo family, keep dependency gates strict:

1. Release `untaped` first.
2. Release leaf tools after `untaped` is on production PyPI.
3. Release `untaped-ansible` after `untaped-github` is on production PyPI.
4. Release `untaped-recipe` last.

Downstream suite repos carry no standing git source pins — internal
dependencies resolve from PyPI in development and CI alike (a dev-only
`[tool.uv.sources]` entry used while co-developing the SDK and a tool is
removed before merging). The release build must use `uv build --no-sources`,
and release checks must prove internal dependency floors resolve from the
target index before upload.

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

If upload succeeds but the post-upload smoke fails, the version may be burned on
that index. Do not retry by overwriting the same version. Bump patch, relock,
open or update the PR, and repeat the TestPyPI/PyPI cycle.

## Follow-Up

After all active packages have completed one TestPyPI and one PyPI cycle, run a
consolidation review:

- Compare duplicated release workflow blocks across repos.
- Keep repo-local workflows unless a shared helper removes real maintenance
  cost without hiding package-specific safety checks.
- Re-check whether contract tests should stay in pytest or move security
  hygiene checks to `zizmor`/`actionlint`.
