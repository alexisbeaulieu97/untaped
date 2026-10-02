# Contributing

This is the developer guide for the `untaped` repository. Rules that every
capability provider follows, first-party or not, live in
[`docs/plugins.md`](docs/plugins.md); this page covers what is specific to
working in this repository.

## Local setup

```bash
uv sync
```

The root `dev` dependency group installs `untaped[all]`, so every first-party
capability is available to `uv run untaped`.

## Test, lint and type-check

```bash
uv run pytest -n auto                           # tests, in parallel (add `--cov` for the 89% coverage gate, as CI does)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run pre-commit run --all-files               # pre-commit hooks
uv run python scripts/release.py check          # release metadata
uv lock --check                                 # lock file is current
```

## Repository layout

- The root `pyproject.toml` is the uv workspace root (tool configuration and
  the `dev` group, no `[project]`); `uv.lock` locks the whole workspace.
- `packages/<name>/` holds one distribution each: its `pyproject.toml`,
  `README.md`, `src/` and `tests/`. Core is `packages/untaped/`: its
  `src/untaped/` holds the root shell and shared services. `sdk.py` is the
  public SDK surface; `capabilities/registry.py` is the internal composition
  kernel (see [How composition works](docs/plugins.md#how-composition-works));
  `management/` holds the root's management commands. The implementation is
  the reference for composition and command behavior.
- Each capability is its own package,
  `packages/untaped-<name>/src/untaped_<name>/`, and owns one capability end
  to end. Its `README.md` is its user guide and its PyPI page.
- `examples/untaped-hello/` is a minimal third-party plugin with its own
  tests; it is not a workspace member and is never published. CI installs it
  beside the core wheel and runs its tests outside the repository.
- `docs/` holds the user guides, the plugin guide and the generated config
  reference.
- `tests/` verifies public behavior and release contracts: `tests/repo/`
  holds the cross-package tests and `tests/skills/` the skill evaluation
  cases; a capability's tests live in `packages/untaped-<name>/tests/<name>/`.

## Adding a first-party capability

A first-party capability follows the [plugin rules](docs/plugins.md#conventions)
and uses this layout:

```
packages/untaped-<name>/src/untaped_<name>/
├── __init__.py        # SPEC: CapabilitySpec (with one-line help) + nullary build_app() (lazy CLI import; never build at import time) + provider()
├── settings.py        # profile model + state model (field sets must be disjoint)
├── api.py             # optional: declared public module other first-party capabilities may import
├── cli/               # cyclopts commands (thin)
├── application/       # use cases (orchestration); ports in application/ports.py
├── domain/            # entities, value objects (pure, no I/O)
├── infrastructure/    # external adapters (httpx clients, fs, …)
├── errors.py          # <Cap>Error(UntapedError) and its subclasses
└── skills/            # packaged agent skills shipped via SPEC.skills
```

1. Add `packages/untaped-<name>/`, exposing `SPEC`, `build_app` and a nullary
   `provider()` returning `SPEC`. Set `SPEC.help` to the app's one-line help
   (see [Settings and the capability app](docs/plugins.md#settings-and-the-capability-app)).
2. In its `pyproject.toml` (copy a sibling package's), add
   `<name> = "untaped_<name>:provider"` under
   `[project.entry-points."untaped.capabilities"]`.
3. Add the `untaped[<name>]` extra to core and a `[tool.uv.sources]` entry,
   then `uv sync`.
4. Add the package to the root `pyproject.toml` lists: mypy
   `files`/`mypy_path`, pytest `testpaths`/`pythonpath`, coverage `source`
   and `[tool.uv.sources]`.
5. Add it to `EXPECTED_MEMBERS` in `tests/repo/test_workspace.py` and to
   `FIRST_PARTY` in `tests/repo/support.py`. The tests catch omissions.
6. Call `untaped.testing.check_conventions` from its tests. The default
   table columns rule is enforced by this repository's own test suite, not by
   `check_conventions`, and `# untaped: allow` does not apply to it.
7. Start its skill from the [skill template](docs/plugins.md#packaged-skills)
   and its user guide as the package `README.md`, linked from the root
   `README.md` and `docs/getting-started.md`.

A capability whose settings import another's `api` (ansible imports
github's) is quarantined with it when that import fails. Shared logic follows
[Depending on another capability](docs/plugins.md#depending-on-another-capability);
in this repository it may also live in core. Extract a protocol into core
only when a second provider appears.

## Workflow

- **TDD.** Write the failing test first, then the smallest implementation.
- **Test through public APIs.** Never suppress warnings to reach private
  members.
- **Grep before writing.** If a helper exists in the wrong place, move it and
  update its callers; don't fork it.
- **Absolute imports only**, tests included.
- **Module docstrings.** Every module opens with a docstring saying what it
  owns (re-export stubs are exempt). Rationale that protects code goes there.
- **Secrets and TLS.** Secrets are `pydantic.SecretStr`; HTTP clients resolve
  TLS through `resolve_verify` (`tests/repo/test_invariants.py` pins both).
- **Docs in the same change.** A change to behavior, settings, composition or
  a shared helper updates its docs in the same commit. Each fact has one home
  and other pages link to it: capability detail in its skill (the package
  `README.md` is a short guide), exit codes, record kinds and environment
  variables in [`docs/scripting.md`](docs/scripting.md), settings in the
  generated [config reference](docs/reference/config.md), history in
  [`CHANGELOG.md`](CHANGELOG.md). Never copy `--help` output, default columns
  or API signatures into docs.
- **Config reference.** After changing a settings model, run
  `uv run python scripts/gen_config_reference.py`; a test fails while the
  reference is stale.

## Releasing

A release is a release PR, a TestPyPI rehearsal whenever the workflow, the
build or the package set changed, and a `vX.Y.Z` tag on `main`.
`.github/workflows/release.yml` does the rest; the workflow and
`scripts/release.py` are the reference for what each step checks.

Publishing, dispatching a release workflow, creating a tag or release,
merging a PR and changing repository settings each need explicit approval for
that exact action, because each changes shared or public state.

### One-time setup

- **Trusted publishers** on both PyPI and TestPyPI: owner `alexisbeaulieu97`,
  repository `untaped`, workflow `release.yml`, environment `pypi` (PyPI) or
  `testpypi` (TestPyPI). Each new PyPI project needs a pending publisher on
  both indexes before its first rehearsal.
- **The `pypi` environment** requires reviewer approval, and its deployment
  rules must allow `v*` tags (not only `main`).
- **A tag ruleset** blocks updating and deleting `v*` tags. It is a
  repository setting, made by hand.

### The release PR

It touches these and nothing else:

- versions: every package version and sibling pin in each
  `packages/*/pyproject.toml`, and on a major `examples/untaped-hello`'s
  `untaped` range (`>=X,<X+1`);
- `uv.lock` (`uv lock`);
- `CHANGELOG.md`: rename `## Unreleased` to `## X.Y.Z`.

A major release collects the breaking changes held back since the last one
(see [Versioning](README.md#versioning)), and its changelog section opens
with `### Upgrading`: one item for each Breaking bullet, saying what a user
or script must do about it. Changes add those items under `## Unreleased` as
they land; the release PR checks the list is current before renaming the
section.

### Rehearse

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

### Release

1. Merge the release PR.
2. Tag the merge commit on `main` `vX.Y.Z` and push the tag.
3. Check that CI is green for that commit, then approve the `pypi`
   environment.

The GitHub release is created from the CHANGELOG section once the published
package installs. Builds use the commit timestamp, so rebuilding a tag gives
byte-identical files.

### A failed run

- Use **Re-run failed jobs**: it reuses the built artifacts.
- Publishing skips files already on the index, and the index check refuses
  different bytes. When it does, bump the patch version.
- A partial multi-package publish is completed by the same re-run.
- The GitHub release step finds a leftover draft by listing releases and
  finishes it. It never changes a published release: one whose assets match
  is a no-op, and one with a mismatch fails.

## Evaluating a skill change

The structure tests check that packaged skills parse and link, not what an
agent does with them. When a change to a skill could change an agent's
behavior, run the cases in [`tests/skills/cases.md`](tests/skills/cases.md)
against the old and new versions, following its method.

First-party skills are also checked by `tests/repo/test_skill_files.py`
(description matches the frontmatter, every quoted command parses, no link
leaves the skill).

## Sensitive data

Do not include secrets, real customer configurations, production logs, private
workspace data, health exports, or other private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.
