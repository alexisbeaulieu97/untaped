# Releasing `untaped`

A release is a release PR, a TestPyPI rehearsal whenever the workflow, the
build or the package set changed, and a `vX.Y.Z` tag on `main`.
`.github/workflows/release.yml` does the rest; the workflow and
`scripts/release.py` are the reference for what each step checks.

Publishing, dispatching a release workflow, creating a tag or release,
merging a PR and changing repository settings each need explicit approval for
that exact action, because each changes shared or public state.

## One-time setup

- **Trusted publishers** on both PyPI and TestPyPI: owner `alexisbeaulieu97`,
  repository `untaped`, workflow `release.yml`, environment `pypi` (PyPI) or
  `testpypi` (TestPyPI). Each new PyPI project needs a pending publisher on
  both indexes before its first rehearsal.
- **The `pypi` environment** requires reviewer approval, and its deployment
  rules must allow `v*` tags (not only `main`).
- **A tag ruleset** blocks updating and deleting `v*` tags. It is a
  repository setting, made by hand.

## The release PR

It touches these and nothing else:

- every package version: today the root `pyproject.toml`; after the split,
  each `packages/*/pyproject.toml` together with its exact sibling pins;
- `uv.lock` (`uv lock`);
- `CHANGELOG.md`: rename `## Unreleased` to `## X.Y.Z`.

A major release collects the breaking changes held back since the last one
(see [Versioning and stability](./stability.md)). Open its changelog section
with an **Upgrading** list, one item for each Breaking bullet: what a user or
script must do about it.

## Rehearse

Rehearse on the release PR branch:

```bash
gh workflow run release.yml --ref <release-pr-branch>
```

This runs the same build and checks, publishes to TestPyPI and installs from
it. TestPyPI files are immutable, so re-rehearsing the same commit is a
no-op. Builds are stamped with the commit time, so the next TestPyPI
rehearsal of an already-rehearsed version from a new commit fails the index
check: rehearse a pre-release such as `X.Y.Zrc1` first, or accept that
`X.Y.Z` cannot be rehearsed again. The tag run checks PyPI only, so a
rehearsal never blocks the release.

## Release

1. Merge the release PR.
2. Tag the merge commit on `main` `vX.Y.Z` and push the tag.
3. Check that CI is green for that commit, then approve the `pypi`
   environment.

The GitHub release is created from the CHANGELOG section once the published
package installs. Builds use the commit timestamp, so rebuilding a tag gives
byte-identical files.

## A failed run

- Use **Re-run failed jobs**: it reuses the built artifacts.
- Publishing skips files already on the index, and the index check refuses
  different bytes. When it does, bump the patch version.
- A partial multi-package publish is completed by the same re-run.
- The GitHub release step finds a leftover draft by listing releases and
  finishes it. It never changes a published release: one whose assets match
  is a no-op, and one with a mismatch fails.
