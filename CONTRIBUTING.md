# Contributing

This is the developer guide for the `untaped` repository. Rules that every
plugin follows, first-party or not, live in
[`docs/reference/conventions.md`](docs/reference/conventions.md); this page
covers what is specific to working in this repository.

## Local setup

```bash
uv sync
```

The root `dev` dependency group installs `untaped[all]`, so every first-party
plugin is available to `uv run untaped`.

To try unreleased changes without a checkout, install from git. The repo root
is a workspace with no package of its own, so the bare repository URL fails to
build, and `untaped[all]` would take the plugin packages from PyPI. Point at
each package directory instead (one line, so it pastes unchanged in bash and
PowerShell):

```
uv tool install --force "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-ansible" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-awx" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-dotfiles" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-git" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-github" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-jira" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-lint" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-recipe" --with "git+https://github.com/alexisbeaulieu97/untaped#subdirectory=packages/untaped-workspace"
```

To pick a ref, put `@<branch-or-sha>` before `#subdirectory=` in each URL.

## Test, lint and type-check

```bash
uv run pytest -n auto                           # tests, in parallel (add `--cov` for the 95% coverage gate, as CI does)
uv run ruff check --fix && uv run ruff format   # lint + format
uv run mypy                                     # strict types
uv run untaped lint                             # this repository's own rules (.untaped/)
uv run pre-commit run --all-files               # pre-commit hooks (vulture included)
uv run python scripts/release.py check          # release metadata
uv lock --check                                 # lock file is current
```

## Repository layout

- The root `pyproject.toml` is the uv workspace root (tool configuration and
  the `dev` group, no `[project]`); `uv.lock` locks the whole workspace.
- `packages/<name>/` holds one distribution each: its `pyproject.toml`,
  `README.md`, `LICENSE`, `src/` and `tests/`. Core is `packages/untaped/`;
  its `src/untaped/` has three roots, named by who reads them, beside the
  experimental `screen/` kit:
  - `sdk/` is what a plugin may import. Its groups are subpackages
    (`storage/`, `contracts/`, `network/`, `health/`, `prompt/`, `testing/`,
    and the `git` and `projects` modules). Today a plugin imports the names
    `untaped.sdk` exports, `untaped.sdk.contracts` and `untaped.sdk.testing`;
    the other groups become importable as they list names, and a
    `_`-prefixed module or name is never public. `sdk/` is moving off
    `runtime/` and `core/`: `tests/repo/test_layout.py` names each SDK module
    that still imports them, and the list only shrinks.
  - `core/` is untaped's own plugin: its settings and its commands
    (`core/commands/`) with their logic.
  - `runtime/` is how a run happens: discovery and composition
    (`runtime/registry.py`, see [How composition works](docs/composition.md)),
    settings loading, the stderr sink and rendering. No plugin imports
    `runtime/` or `core/`.

  The implementation is the reference for composition and command behavior.
  Core's tests mirror the source under `tests/untaped/` (the test of
  `untaped/sdk/storage/cache.py` is `tests/untaped/sdk/storage/test_cache.py`);
  helpers several tests share live in `tests/core_test_support/`.
- Each plugin is its own package,
  `packages/untaped-<name>/src/untaped_<name>/`, and owns one plugin end
  to end. Its agent pages (`untaped agents <name>`) are the reference; its
  `README.md` (also its PyPI page) is a one-screen guide that points at them.
  A package README links into this repository with absolute `blob/main`
  URLs, which the release build points at its own `vX.Y.Z` tag.
- `examples/` holds third-party plugins with their own tests: `untaped-hello`
  (a minimal plugin) and `untaped-shelf` and `untaped-library` (a contract
  owner and its provider). They are not workspace members and are never
  published. CI installs each beside the core wheel and runs its tests
  outside the repository.
- `docs/` holds the user guides and the plugin guide; `docs/reference/`
  holds lookup pages (settings, records, exit codes, environment variables,
  conventions). Each page stays at most 400 lines long; split one by reader task
  rather than letting it grow (`house.docs-page-length`).
- `.untaped/` holds this repository's own lint rules (`lint.yml` and the
  modules in `lint/`, tested in `tests/lint/`): conventions that used to be
  prose, which `untaped lint` checks at the root and CI runs.
- `tests/` verifies public behavior and release contracts: `tests/repo/`
  holds the cross-package tests and `tests/skills/` the agent evaluation
  cases; a plugin's tests live in `packages/untaped-<name>/tests/<name>/`.

## Adding a first-party plugin

A first-party plugin is treated like any third-party plugin: it uses only
`untaped.sdk`, `untaped.sdk.contracts`, `untaped.sdk.testing` and other plugins'
declared `api` modules, and gets no special treatment in how it is loaded or
what it may use, because any of them may move to its own repository. It
follows the [plugin rules](docs/reference/conventions.md) and uses this layout:

```
packages/untaped-<name>/src/untaped_<name>/
├── __init__.py        # SPEC: PluginSpec (with one-line help) + nullary build_app() (lazy CLI import; never build at import time)
├── settings.py        # settings model + state model (field sets must be disjoint)
├── api.py             # optional: declared public module other plugins may import
├── cli/               # cyclopts commands (thin)
├── application/       # use cases (orchestration); ports in application/ports.py
├── domain/            # entities, value objects (pure, no I/O)
├── infrastructure/    # external adapters (httpx clients, fs, …)
├── errors.py          # <Name>Error(UntapedError) and its subclasses
└── guides/            # optional: agent guides shipped via SPEC.guides
```

1. Add `packages/untaped-<name>/` with a copy of the root `LICENSE`, exposing
   `SPEC` and `build_app`. Set
   `SPEC.help` to the app's one-line help (see
   [Settings and the plugin app](docs/plugins.md#settings-and-the-plugin-app)).
2. In its `pyproject.toml` (copy a sibling package's), add
   `<name> = "untaped_<name>:SPEC"` under
   `[project.entry-points."untaped.plugins"]`.
3. Add the `untaped[<name>]` extra to core and add the package to core's
   `all` extra, then `uv sync`.
4. Add the package to the root `pyproject.toml` lists: mypy
   `files`/`mypy_path`, pytest `testpaths`/`pythonpath`, coverage `source`,
   vulture `paths` and `[tool.uv.sources]`.
5. Add it to `EXPECTED_MEMBERS` in `tests/repo/test_workspace.py` and to
   `FIRST_PARTY` in `tests/repo/support.py`. The tests catch omissions.
6. Call `untaped_lint.testing.assert_clean` from its tests. The default
   table columns rule is enforced by this repository's own test suite, not by
   `untaped lint`, and `# untaped: allow` does not apply to it.
7. Give it a stub skill ([Writing a plugin's skill](docs/plugin-skills.md)).
   Start its package `README.md` from a sibling's (the same section order,
   ending in `## Reference`), linked from the root `README.md` and
   `docs/getting-started.md`.

A plugin whose settings import another's `api` (ansible imports
github's) is quarantined with it when that import fails. Shared logic follows
[Depending on another plugin](docs/reference/conventions.md#depending-on-another-plugin),
or becomes an SDK helper that every plugin can use. Declare a
[contract](docs/contracts.md) once a second plugin would fill it.

## Workflow

- **TDD.** Write the failing test first, then the smallest implementation.
- **Test through public APIs.** Never suppress warnings to reach private
  members.
- **Grep before writing.** If a helper exists in the wrong place, move it and
  update its callers; don't fork it.
- **Absolute imports only**, tests included.
- **Lazy imports on CLI startup paths** (`tests/repo/test_import_cost.py`
  budgets them); `# noqa: PLC0415` only where Ruff flags it.
- **Git through `run_git` and `git_toplevel`**, and advisory file locks
  through `file_lock`, all exported by `untaped.sdk`; never hand-rolled
  `subprocess` git plumbing.
- **Module docstrings.** Every module opens with a docstring saying what it
  owns (re-export stubs are exempt; `house.module-docstring` checks source
  modules). Rationale that protects code goes there.
- **Secrets and TLS.** Secrets are `pydantic.SecretStr`; HTTP clients resolve
  TLS through `resolve_verify` (`tests/repo/test_invariants.py` pins both).
- **Docs in the same change.** A change to behavior, settings, composition or
  a shared helper updates its docs in the same commit. Each fact has one home
  and other pages link to it: plugin detail in the command's docstring, the
  record's model or the plugin's guide, which its agent pages print (the
  package `README.md` is a one-screen guide: set up, one example per
  workflow, and a `## Reference` section pointing at `untaped agents
  <name>`), exit codes, record kinds and environment
  variables in [`docs/reference/`](docs/reference), settings in the
  generated [config reference](docs/reference/config.md), history in
  [`CHANGELOG.md`](CHANGELOG.md). Never copy `--help` output, default columns
  or API signatures into docs.
- **Config and state format.** Within a major, a core-owned key may be added
  to `config.yml` or `state.yml` only if an older reader ignoring it is safe;
  anything else (TLS verification, a proxy, anything security-relevant) is a
  `format_version` bump, which only a major makes.
- **Config reference.** After changing a settings model, run
  `uv run python scripts/gen_config_reference.py`; a test fails while the
  reference is stale.
- **Public surface.** After changing a command, setting, SDK export or
  contract, run `uv run python scripts/gen_surface.py` and commit
  `tests/repo/surface.txt`. A test fails while it is stale, and in a minor
  release when it loses a stable line of the last release; the rules behind
  the lines are in `untaped/core/surface/__init__.py`.

## Before you open a PR

Tasks live in this repository's GitHub issues. CI checks what a machine can:
tests, coverage (95% overall, 90% of the lines a PR changes), types, lint,
dead code, links, every quoted `untaped` command, and the PR's drift review
(`scripts/check_pr.py`). Review your own diff against this checklist and
answer each item, in order, in the PR template's **Drift review** section
(what you checked, or `n/a`):

- **Docs, skills and READMEs.** Every page that describes what changed still
  says something true, in the fact's one home (see Workflow), and names any
  new command, option or setting.
- **Changelog.** A user-visible change adds a fragment,
  `changelog.d/<slug>.<type>.md`: a sentence saying what a user notices or
  must do; details belong in the PR and the docs. Refactors and test-only
  changes get none. A change to `packages/*/src` without one answers
  `none, <why>`; answer with the fragment's path. See
  [Changelog fragments](#changelog-fragments).
- **Duplicated helpers.** Nothing new repeats a helper in `untaped.sdk`,
  core or another plugin; move a misplaced helper instead of forking it.
- **Repo rules.** The Workflow rules above that no linter checks: lazy
  imports, git and locks through core, `SecretStr`, test-module docstrings.
- **Issues.** `Closes #N` for the issue the PR finishes, and any open issue
  the diff makes stale or already finishes.

### Changelog fragments

Never edit `CHANGELOG.md` in a feature PR; it lists released versions only.
`<type>` is `added`, `changed`, `deprecated`, `removed`, `fixed` or
`upgrading`, and `<slug>` is free kebab-case (`settings-marks`). The file holds
one entry: the sentence, wrapped at 78 columns (the bullet adds two; only a
line holding one longer token, such as a URL, may exceed it), with no leading
`- `, heading or blank line. A breaking change keeps the
`**Breaking (scope):**` prefix and comes with an `upgrading` fragment saying
what a user or script must do. Two user-visible changes are two fragments; a
change to something still unreleased edits its fragment.

```text
changelog.d/doctor-fix.added.md:
`untaped doctor fix` runs every automatic fix, re-checks, and reports what
changed.
```

The release adds each entry's PR link itself (`([#N](…))`, from the PR that
merged the fragment); an entry that ends with its own link group, such as an
issue link, keeps it. `scripts/changelog.py check` validates the fragments
(a pre-commit hook), and `scripts/changelog.py draft` prints the next
release's section.

### Dead code

`vulture` (run by pre-commit) fails on code nothing uses. Delete it, or, for
a false positive such as an emitted record field, add `Owner.name  # why` to
`scripts/vulture_allowlist.py`. Decorator-registered code and fixed framework
signatures are ignored in `[tool.vulture]`, not listed.

## Releasing

Plan releases with GitHub milestones, one for the next minor and one for the
next major; each issue sits in the milestone it should ship in. Features
merge to `main` when ready, each with its changelog fragment, so `main` stays
releasable as a minor. A breaking change waits in the next major's milestone,
and its PR merges only once that major is the next release; where it can, a
deprecation warning ships in a minor first. A release is cut when its
milestone's issues are closed.

A release is a release PR, a TestPyPI rehearsal whenever the workflow, the
build or the package set changed, and the merge of the release PR.
`.github/workflows/release.yml` does the rest: a push to `main` that changes
the version builds it, waits for the `pypi` approval, publishes, tags the
built commit `vX.Y.Z` and creates the GitHub release. The workflow and
`scripts/release.py` are the reference for what each step checks.

Publishing, dispatching a release workflow, creating a tag or release,
merging a PR and changing repository settings each need explicit approval for
that exact action, because each changes shared or public state. Merging the
release PR is the decision to release, so only the maintainer merges it,
never an agent.

### One-time setup

- **Trusted publishers** on both PyPI and TestPyPI: owner `alexisbeaulieu97`,
  repository `untaped`, workflow `release.yml`, environment `pypi` (PyPI) or
  `testpypi` (TestPyPI). Each new PyPI project needs a pending publisher on
  both indexes before its first rehearsal.
- **The `pypi` environment** requires reviewer approval: it is the only
  human step between a version-changing merge and PyPI. Its deployment rules
  allow both `main` and `v*` tags, and "Prevent self-review" stays off, since
  the maintainer who merges the release PR also approves its run.
- **A tag ruleset** blocks updating and deleting `v*` tags, not creating
  them, so the release run can tag. It is a repository setting, made by hand.

### The release PR

It touches these and nothing else:

- versions: every package version and sibling pin in each
  `packages/*/pyproject.toml`, and on a major each `examples/*` plugin's
  `untaped` range (`>=X,<X+1`);
- `uv.lock` (`uv lock`);
- `CHANGELOG.md`, `changelog/` and `changelog.d/`: run
  `uv run python scripts/changelog.py build X.Y.Z`, which writes `## X.Y.Z`,
  deletes the fragments and, on a major, moves the previous major's sections
  to `changelog/<X-1>.x.md` and links it under `## Older releases`. Preview
  the section first with `changelog.py draft`. A pre-release (`X.Y.Zrc1`)
  writes `## X.Y.Zrc1`; a later build of the same version retitles that
  section and adds new fragments. Only a release PR may edit these paths, and
  it fails while any fragment is left. `build` also writes a Deprecated line
  for each removal version raised since the newest release tag, so run
  `git fetch --tags origin` first; it refuses to run without a tag.

Nothing may still be marked removed in the major `main` carries (from
`X.0.0rc1` on, X), so a major's removals land in prep PRs before its release
PR: `uv run python scripts/gen_surface.py --due` lists them. To keep one
longer instead, raise its `removed_in`, regenerate the surface and add a
`deprecated` fragment saying why
([Deprecating something](docs/reference/deprecation.md)).

A major release's changelog section (see [Versioning](docs/versioning.md))
opens with `### Upgrading`: one item for each Breaking entry, saying what a
user or script must do about it. Each Breaking change adds its `upgrading`
fragment as it lands (the PR check requires it), and `build` refuses an
`upgrading` fragment in a minor or patch release.

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
`X.Y.Z` cannot be rehearsed again. The release run checks PyPI only, so a
rehearsal never blocks the release.

### Release

1. Merge the release PR.
2. Check that a Release run started on the merge commit (its `route` job
   says why it releases, or why not). No run, or one that failed with no
   jobs, means the trigger or `release.yml` is broken: see below.
3. Once CI is green for that commit, approve the `pypi` environment when the
   run asks.

After PyPI has the files and the published package installs, the run tags
the built commit `vX.Y.Z` ("untaped X.Y.Z") and creates the GitHub release
from the CHANGELOG section. A push to `main` that leaves the version as it
was, such as a dependency bump, ends at `route` with nothing published. A
fork's runs publish nothing.

### A failed run

- Use **Re-run failed jobs**: it reuses the built artifacts.
- Publishing skips files already on the index, and the index check refuses
  different bytes. When it does, bump the patch version.
- A partial multi-package publish is completed by the same re-run.
- The GitHub release step finds a leftover draft by listing releases and
  finishes it. It never changes a published release: one whose assets match
  is a no-op, and one with a mismatch fails.
- A rejected approval publishes nothing and leaves the version untagged.
  Re-run failed jobs to be asked again (GitHub offers re-runs for 30 days
  from the run's start), push the tag by hand, or merge the next release PR.
  An approval left waiting expires after those 30 days, with the re-run, so
  then only the hand tag or the next release PR remain.
- A cancelled Release run (a newer pending run in the same concurrency group
  replaced it): **Re-run all jobs** routes it again.
- Never re-run a release run that went green.
- The tag step is idempotent: a tag already on the built commit passes, and
  one on another commit fails, because `v*` tags never move. Then release a
  patch version, or relax the ruleset, delete the wrong tag, re-run and
  restore the ruleset.
- When the `main` run cannot release (no run, re-runs expired, a broken
  workflow), push the tag by hand: `git tag -a vX.Y.Z -m "untaped X.Y.Z"
  <sha>` and `git push origin vX.Y.Z`. That starts a release run for the
  tag, with the workflow at the tagged commit, so after a workflow fix tag
  the fix's commit on `main`, not the release PR's merge commit (tags never
  move). If anything was already published from the earlier commit, release
  a patch version instead: the new commit builds different bytes. Only push
  a tag when the `main` run did not release: both runs ask for approval.
- If no workflow can run at all, the tag and the GitHub release can be made
  by hand (`gh release create vX.Y.Z --verify-tag --notes-file ...` with the
  built files), and the packages built with
  `uv build --all-packages --no-sources` from a checkout of the commit, with
  `SOURCE_DATE_EPOCH` set to its commit time so the files match any already
  uploaded, then published with a PyPI API token through `uv publish`.

## Evaluating an agent docs change

`untaped lint` and the agent pages tests check that the pages render and
that what they quote parses, not what an agent does with them. When a change
to a stub description, a guide or a command's docstring could change an
agent's behavior, run the cases in [`tests/skills/cases.md`](tests/skills/cases.md)
against the old and new versions, following its method.

## Live AAP smoke (awx)

Unit tests mock AAP. To try the awx write path against a real controller
(opt-in), pick a disposable inventory and a harmless source, run a smoke like
this, and restore the exported files afterward:

```bash
untaped awx ping
untaped awx inventories export Disposable --organization Default \
  --out disposable-inventory.yml
untaped awx inventory-sources export DisposableSource --inventory Disposable \
  --inventory-organization Default --out disposable-source.yml

untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --dry-run
untaped awx inventory-sources patch DisposableSource \
  --inventory Disposable --inventory-organization Default \
  --set update_cache_timeout=0 --yes
untaped awx inventory-sources get DisposableSource \
  --inventory Disposable --inventory-organization Default --format yaml

untaped awx inventory-sources edit DisposableSource \
  --inventory Disposable --inventory-organization Default --dry-run
untaped awx inventory-sources sync DisposableSource \
  --inventory Disposable --inventory-organization Default --follow

untaped awx apply disposable-source.yml --yes
untaped awx apply disposable-inventory.yml --yes
```

Confirm that the cache timeout changed, `update_on_launch` stayed unchanged,
the no-op editor made no write, and the sync reached the expected terminal
state. These steps are opt-in live writes against a disposable controller.

## Sensitive data

Do not include secrets, real customer configurations, production logs, private
workspace data, personal data, or other private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.

## Glossary

Terms these docs and the code use; see the [README](README.md) for what
untaped is.

**Plugin**: what `untaped` composes under one name, which is also its
command group, config section and data directory, and the distribution
`untaped-<name>` that ships it. It may add commands, settings, state,
skills, doctor checks and contracts; each is optional.

**Contract**: an interface one plugin declares and others fill, so the
plugin asking never names who answers.

**Owner**: the plugin that declares a contract, or an item store and so
decides how its items are released.

**Provider**: a plugin that fills a contract, or the class in it that does.

**Source**: three things that keep the word. A record's `source` names the
plugin and record it came from (provenance on the wire); ansible's `source`
is a set of repositories a user adds (`ansible source add`); and
`RepoSource` is a contract.

**Answer cache**: the SDK's store of a cached contract answer, per
provider, method, profile and arguments, kept with its freshness in one file
under the owner's `cached/<contract>_<method>/`.

**Repo store**: the git plugin's shared bare repositories under
`plugins/git/store/`, which every consumer's refs and worktrees depend on.

**State**: data a plugin keeps as the only copy, in `state.yml`, through
`StateRows` (typed rows) or `StateMap` (strings).

**Cache**: upstream data a plugin keeps and can fetch again, checked once it
is older than its max age; see [Caches](docs/caches.md).

**Max age**: how long cached data is served without checking upstream; a
cache's declared default, tunable per cache.

**Stale**: older than its max age, or last refreshed by a run that failed or
stopped early.

**Refresh**: consulting upstream and updating the cache; it fetches only
what changed.

**Directive**: what the user asked of a cache: auto (refresh when stale; the
default), refresh (check upstream now) or cached (never check).

**Diagnostic**: a typed stderr line, with a `level`, a `kind` and a
`message`.

**Sink**: the one writer to stderr.

**Policy**: one invocation's output choices.

**Tree output**: a graph drawn as an indented tree, shared branches numbered
and cycles marked (`--format tree`, `ansible graph`); not the lint Tree.

**Rule**: one check `untaped lint` runs, with a public id `<key>.<id>`
(`lint.error-case`, `house.docs-page-length`), a severity and the record kind
it reads.

**Finding**: what a rule reports about one target; an error finding fails the
run.

**Subject**: a record a run lints, by its kind (`untaped.plugin`, `lint.tree`).

**Tree**: the files of a repository a run lints: what git tracks or would
track, minus what `.untaped/lint.yml` excludes.

**Linter**: the contract a plugin fills to add rules to `untaped lint`; a
repository's own rule modules fill it under the lint file's key.

**Reader**: a plugin that fills `TreeReader`, turning the files a
`readers:` entry names into its own subjects.

**Origin**: where a linted AWX resource was read: an apply document or the
controller. A finding's location says which (`templates.yml#2`,
`job_templates/14`).

**Setting**: one value a user tunes, named `section.key`, set in a profile or
by an `UNTAPED_*` environment variable.

**Profile**: a named set of settings in `config.yml`; the profiles a run picks
are layered over the base, `default`.

**Project**: a folder whose `.untaped/` holds `project.json`, the nearest one
found by walking up from the working directory; it need not be a git
checkout's root. The home records it by the folder's filesystem identity, so
a move or a copy is told apart from the same folder.

**Holder**: what holds items in the home's references: a project, today.

**Item store**: the items a plugin keeps for holders (workspace's repos),
declared as an `ItemStore`; its plugin is their owner.

**Hold**: one item a holder uses, recorded in its reference; it is let go
only through the item's owner.

**Global option**: one of the options that work anywhere in a command
(`--profile`, `--verbose`, `--quiet`, `--deprecated`); the code calls them
root options (`_root_options.py`).

**Renamed key**: an old name of a setting that still works, with a warning,
until its removal version.

**Retired key**: an old name of a setting that is no longer read;
`untaped config migrate` still renames it.

**Changelog fragment**: one user-visible change, written as a file in
`changelog.d/` and gathered into `CHANGELOG.md` at release.

**Stability**: whether a command or setting is stable, experimental or
deprecated.

**Experimental**: may change or go away in a minor release; said in its
`--help`.

**Deprecated command**: a command that still works, with a warning, until
its removal version.

**Deprecated setting**: a setting still read as before, with a warning,
until its removal version.

**Removal version**: the major release that removes a deprecated thing,
named in its declaration (`removed_in=`) and shown in its warning.

**Fix**: the `untaped` command a doctor row names to resolve what it found.

**Automatic fix**: a fix that needs no value and no input, so
`untaped doctor fix` runs it.

**Agent page**: what `untaped agents` prints for a coding agent: the map,
a plugin's page or a topic's page, generated from what core and the plugins
declare.

**Stub skill**: a version-neutral agent skill that untaped writes from its
own template and a plugin's description; it lists no command and sends the
agent to `untaped agents`.

**Topic**: what a plugin's agent page lists and `untaped agents PLUGIN TOPIC`
prints: one of its commands, or a guide.

**Guide**: a topic a plugin writes as a Markdown file, for what belongs to no
single command; each says when to read it.

**Screen**: an interactive terminal UI built on the SDK's runtime.
