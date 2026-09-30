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

Recipe verbs (`apply`, `list`, `get`, `edit`, `init`, `validate`, `test`) sit
directly under `untaped recipe`. Packs, hooks and backups have their own
nouns: `recipe packs …`, `recipe hooks …` and `recipe backups …`.

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
untaped recipe list
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --dry-run
untaped recipe apply acme/editorconfig ~/work/api ~/work/web
untaped recipe apply ./my-pack/recipes/editorconfig/recipe.yml ~/work/api --yes
untaped recipe apply acme/editorconfig ~/work/api --preview diff
```

The recipe argument is a unique recipe name, a `pack/recipe` ref, or a path.
A value is a path only when it starts with `./`, `../`, `/` or `~`, is `.` or
`..`, or ends in `.yml`/`.yaml`. For a local pack directory, pass its path and
`--recipe NAME`.

`--dry-run` previews without writing; pack hooks still run, because they
compute the plan. A target that fails to plan or write changes nothing and
is reported; the other targets still run.

### Check for drift in CI

```bash
untaped recipe apply acme/editorconfig ~/work/api --check
```

`--check` writes nothing and asks nothing, and exits `3` when any target
would change.

### Apply to every repo of a workspace

`--stdin` reads target paths, or records with a `target_path`:

```bash
untaped workspace repos list prod --format pipe \
  | untaped recipe apply acme/editorconfig --stdin --dry-run
```

With `--stdin` the confirmation reads the terminal. Without one, pass
`--yes`, `--dry-run` or `--check`.

### Inputs

Recipes declare inputs. Give them values with `--var KEY=VALUE` or YAML
files, the same flags `awx test` uses for suite variables:

```bash
untaped recipe apply acme/codeowners ~/work/api --var owner=@acme/platform
untaped recipe apply acme/codeowners ~/work/api --vars-file base.yml --vars-file prod.yml
untaped recipe apply acme/labels ~/work/api --var 'labels=[infra, tls]'
untaped recipe apply acme/readme --stdin --input-from 'service={{ target.name }}' < dirs.txt
```

A later file wins over an earlier one, and `--var` wins over every file.
`--vars-file` values are YAML, so quote version-like strings
(`python_version: "3.10"`). `--input-from` derives a value per target. A
required input still missing is prompted for at a terminal; otherwise the
run fails naming it. Sensitive inputs show as `***` in rows, previews and
backups.

## Install and manage packs

Installing a pack installs code: its hooks run on your machine with no
sandbox, including during `apply --dry-run` and `--check`, since hooks compute
the planned changes. Inspect a pack before you trust it (`recipe packs get`,
`recipe validate`, `recipe test`). Hooks get a reduced environment without
your tokens, but they run as you with full file access; the
[trust model](../../src/untaped/capabilities/recipe/skills/untaped-recipe/references/library.md#backups-and-safety)
lists exactly what they can see.

```bash
untaped recipe packs add https://github.com/acme/untaped-recipes.git --rev v1.2.0
untaped recipe packs add ./my-pack --name acme --force
untaped recipe packs sync --all --dry-run
untaped recipe packs sync acme
untaped recipe packs list
untaped recipe packs get acme
untaped recipe packs edit acme
untaped recipe get acme/editorconfig
untaped recipe edit acme/editorconfig
untaped recipe validate
untaped recipe packs remove acme --yes
untaped recipe packs list --format pipe | untaped recipe packs sync --stdin
```

- A pack must contain a `uv.lock` and no symlinks. Reinstalling needs
  `--force`; local edits to the installed copy also need `--discard-edits`.
- `packs sync` re-fetches packs from the source and `--rev` recorded at
  install (a branch or tag moves forward). Packs whose files would change
  are listed, with the hook code that changes, and confirmed first.
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

| Step | Fields | Does |
|---|---|---|
| `validate` | `hook`, `args` | Runs a hook that returns pass, fail or skip for the target. |
| `transform` | `hook`, one of `file`/`files`/`globs`, `exclude`, `optional`, `args` | Rewrites file content through a hook. |
| `template` | `template`, `dest`, `unknown_tokens`, `if_absent` | Renders a template file into the target. |
| `copy` | `source`, `dest`, `if_absent` | Copies a file as is, binary files included. |
| `remove` | one of `file`/`files`/`globs`, `exclude` | Deletes files. |

`globs` has no implicit excludes: add `exclude: [".git/**"]` when the
targets are Git clones.

### Hooks

A hook module exports `transform()`, `validate()`, or both. `hooks init`
writes a typed stub and a pytest for it. For YAML files, use the built-in
`yaml_edit` hook shown above. Debug one hook without a recipe:

```bash
untaped recipe hooks run acme/pin_python --target ~/work/api --file pyproject.toml --diff
untaped recipe hooks run acme/pin_python --target ~/work/api --file pyproject.toml \
  --var python_version=3.14 --args-file args.yml
```

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
untaped recipe backups list
untaped recipe backups get latest
untaped recipe backups restore latest --dry-run
untaped recipe backups restore latest
untaped recipe backups prune --keep 20
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
