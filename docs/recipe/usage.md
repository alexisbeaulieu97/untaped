# Recipes

`untaped recipe` applies the same file changes to many directories: add a
config file, bump a version in YAML, remove a stale workflow. A *recipe* is a
list of steps; *packs* bundle recipes with Python *hooks*. Every change is
planned in memory, previewed, and written only after you confirm, with a
backup of every file it touches.

Recipes work on plain directories and never commit, push or open pull
requests. No recipe step runs a shell command, but pack hooks are Python
code that runs on your machine (see [Install and manage
packs](#install-and-manage-packs)).

Recipe verbs sit directly under `untaped recipe`; packs, hooks and backups
have their own nouns (`recipe packs …`, `recipe hooks …`, `recipe backups …`).
The packaged skill is the full reference:
[applying](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/apply.md),
[packs, tests and backups](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/library.md)
and [authoring](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/authoring.md).

## Set up

Recipes need no setup beyond installing a pack (below). `uv` must be on your
`PATH` to run pack hooks. Settings such as `recipe.library_root` (where packs
are installed) and hook timeouts are in the
[configuration reference](../reference/config.md#recipe).

## Apply a recipe

```bash
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --dry-run
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --preview diff
untaped recipe apply acme/editorconfig ~/work/api ~/work/web
```

The recipe argument is a unique recipe name, a `pack/recipe` ref, or a path.
A value is a path only when it starts with `./`, `../`, `/` or `~`, is `.` or
`..`, or ends in `.yml`/`.yaml`. For a local pack directory, pass its path and
`--recipe NAME`.

`--dry-run` previews without writing; pack hooks still run, because they
compute the plan. A target that fails to plan or write changes nothing and
is reported; the other targets still run. Within a target, writes are one
transaction.

### Check for drift in CI

```bash
untaped recipe apply acme/editorconfig ~/work/api --check
```

`--check` writes nothing, asks nothing and makes no backup, and exits `3`
when any target would change.

### Apply to every repo of a workspace

`--stdin` reads target paths, or records with a `target_path`:

```text
untaped workspace repos list prod --format pipe \
  | untaped recipe apply acme/editorconfig --stdin --dry-run
```

With `--stdin` the confirmation reads the terminal. Without one, pass
`--yes`, `--dry-run` or `--check`.

### Inputs

Recipes declare inputs. Give them values with `--var KEY=VALUE` or YAML
files, the same flags `awx test` uses for suite variables:

```bash
untaped recipe apply acme/codeowners ~/work/api --vars-file base.yml --var owner=@acme/platform
untaped recipe apply acme/readme --stdin --input-from 'service={{ target.name }}' < dirs.txt
```

`--var` wins over every file, and a later file over an earlier one.
`--vars-file` values are YAML, so quote version-like strings
(`python_version: "3.10"`). `--input-from` derives a value per target. A
required input still missing is prompted for at a terminal; otherwise the
run fails naming it. Sensitive inputs show as `***` in rows, previews and
backups, and their targets show no diff. The full precedence and prompt
rules are in the [apply reference](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/apply.md#inputs).

## Install and manage packs

Installing a pack installs code: its hooks run on your machine with no
sandbox, including during `apply --dry-run` and `--check`, since hooks compute
the planned changes. Inspect a pack before you trust it (`recipe packs get`,
`recipe validate`, `recipe test`). Hooks get a reduced environment without
your tokens, but they run as you with full file access; the
[environment list](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/library.md#what-hooks-can-reach)
says exactly what they can see.

```bash
untaped recipe packs add https://git.example.com/acme/untaped-recipes.git --rev v1.2.0
untaped recipe validate acme
untaped recipe packs sync --all --dry-run
```

- A pack must contain a `uv.lock` and no symlinks. Reinstalling needs
  `--force`; local edits to the installed copy also need `--discard-edits`.
- `packs sync` re-fetches packs from the source and `--rev` recorded at
  install, so a branch or tag moves forward. It lists the packs whose files
  would change, with the hook code that changes, and confirms first: review
  that list as you would a dependency upgrade.
- `recipe validate` checks the whole library, or one pack, recipe or path,
  without importing hook code; any failing check exits 1.

## Write a pack

```bash
untaped recipe packs init acme
untaped recipe init ./acme/editorconfig
untaped recipe hooks init ./acme/pin_python --kind transform
```

Each `init` refreshes the pack's `uv.lock`, which needs access to a package index.
`--no-lock` skips that, but hooks cannot run until `uv lock` succeeds.

A pack is a Python project. Its `pyproject.toml` lists recipes and hooks under
`[tool.untaped_recipe.recipes]` and `[tool.untaped_recipe.hooks]`.

### Recipe file

```yaml
version: 1
description: Add a standard .editorconfig and pin the Python version
inputs:
  python_version:
    type: str
    default: "3.14"
steps:
  - type: template
    template: templates/editorconfig
    dest: .editorconfig
    if_absent: true
  - type: transform
    hook: yaml_edit
    file: .pre-commit-config.yaml
    optional: true
    args:
      edits:
        - op: set
          path: [default_language_version, python]
          value: "python{{ python_version }}"
  - type: remove
    file: .travis.yml
```

A `validate` step runs a hook that passes, fails or skips the target;
`transform` rewrites file content through a hook; `template`, `copy` and
`remove` render, copy and delete files. Step fields are in the
[authoring reference](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/authoring.md#recipe-files).
`globs` has no implicit excludes: add `exclude: [".git/**"]` when the
targets are Git clones.

### Hooks

A hook module exports `transform()`, `validate()`, or both. `hooks init`
writes a typed stub and a pytest for it. For YAML files, use the built-in
`yaml_edit` hook shown above. Debug one hook without a recipe:

```bash
untaped recipe hooks run acme/pin_python --target ~/work/api --file pyproject.toml \
  --var python_version=3.14 --diff
```

Hooks compute the plan, so they must only read the target tree and their
own pack: no writes, no network.

### Golden tests

`recipe init` also creates a test case under `tests/RECIPE/CASE/`: `given/`
is the starting directory, `expected/` the full expected result (omit it to
assert no change), and an optional `case.yml` sets inputs and expectations.

```bash
untaped recipe test ./acme
untaped recipe test acme/editorconfig --update
```

`--update` rewrites `expected/` from the current plan.

## Backups

Each apply writes one backup bundle unless you pass `--no-backup`.

```bash
untaped recipe backups restore latest --dry-run
untaped recipe backups restore latest
untaped recipe backups prune --keep 20 --dry-run
```

`restore` refuses to overwrite a file that changed after the backup unless
you pass `--force`. Backups hold file content only, not modes or times.

## Output

`apply` prints one `recipe.apply_outcome` row per target, with `action`
`planned`, `applied`, `unchanged`, `skipped`, `cancelled` or `failed`; the
preview goes to stderr. See [Pipes and record kinds](../reference/pipes.md#recipe)
and [Exit codes](../reference/exit-codes.md).

## See also

- [Workspaces](../workspace/usage.md)
- [Configuration reference](../reference/config.md#recipe)
