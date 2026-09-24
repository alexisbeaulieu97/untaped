# Recipes

`untaped recipe` applies the same file changes to many directories: add a
config file, bump a version in YAML, remove a stale workflow. A *recipe* is a
list of steps; *packs* bundle recipes with Python *hooks*. Every change is
planned in memory, previewed, and written only after you confirm, with a
backup of every file it touches.

Recipes work on plain directories. They run no shell commands and never
commit, push or open pull requests.

## Apply a recipe

```bash
untaped recipe list
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --dry-run
untaped recipe apply acme/editorconfig ~/work/api ~/work/web
untaped recipe apply ./my-pack/recipes/editorconfig/recipe.yml ~/work/api --yes
```

The recipe argument is a unique recipe name, a `pack/recipe` ref, or a path.
A value is a path only when it starts with `./`, `../`, `/` or `~`, is `.` or
`..`, or ends in `.yml`/`.yaml`. For a local pack directory, pass its path and
`--recipe NAME`. A pack name or pack path alone selects the pack's recipe when
it has exactly one.

| Flag | Effect |
|---|---|
| `--dry-run` | Plan and preview; write nothing, back up nothing. |
| `--check` | Write nothing, ask nothing; exit 3 if any target would change. For CI. |
| `--yes` | Skip the confirmation. |
| `--preview table\|diff\|none` | Preview style on stderr. `diff` prints unified diffs. |
| `--no-backup` | Skip the backup bundle. |
| `-j N` | Plan targets in parallel (at most 32). |

A target that fails to plan or write changes nothing and is reported; the
other targets still run. Within one target, writes roll back on failure.

### Apply to every repo of a workspace

`--stdin` reads target paths, or records with a `target_path`:

```bash
untaped workspace get --workspace prod --format pipe \
  | untaped recipe apply acme/editorconfig --stdin --dry-run
```

With `--stdin` the confirmation reads the terminal. Without one, pass
`--yes`, `--dry-run` or `--check`.

### Inputs

Recipes declare inputs. Give them values with `--var` or a YAML file:

```bash
untaped recipe apply acme/codeowners ~/work/api --var owner=@acme/platform
untaped recipe apply acme/codeowners ~/work/api --vars-file inputs.yml
untaped recipe apply acme/labels ~/work/api --var 'labels=[infra, tls]'
untaped recipe apply acme/readme --stdin --input-from 'service={{ target.name }}' < dirs.txt
```

- `--var` wins over `--vars-file`. Unknown input names are rejected.
- `list` and `dict` inputs parse `--var` values as YAML.
- `--input-from NAME=TEMPLATE` derives a value per target from
  `target.path`, `target.name`, `target.parent_path`, `target.parent_name` or
  the piped `record`.
- `--interactive` prompts for inputs that are still missing.
- For each input, the first value found wins: `--var`/`--vars-file` or
  `--input-from`, the recipe's `from`, the prompt, the recipe's `default`.
  Otherwise the target fails with `missing required input`.

Sensitive inputs show as `***` in rows, previews and backups.

## Install and manage packs

Installing a pack installs code: its hooks run on your machine with no
sandbox. Inspect a pack before you trust it (`recipe get`, `recipe validate`,
`recipe test`).

```bash
untaped recipe add https://github.com/acme/untaped-recipes.git --rev v1.2.0
untaped recipe add ./my-pack --name acme --force
untaped recipe list --packs
untaped recipe get acme/editorconfig
untaped recipe validate
untaped recipe edit acme/editorconfig
untaped recipe remove acme --yes
```

- A pack must contain a `uv.lock`. Reinstalling needs `--force`; local edits
  to the installed copy also need `--discard-edits`.
- Packs are installed under `recipe.library_root`.
- `recipe validate` checks the whole library, or one pack, recipe or path,
  without importing hook code.

## Write a pack

```bash
untaped recipe init pack acme
untaped recipe init recipe ./acme/editorconfig
untaped recipe init hook ./acme/pin_python --kind transform
```

`init` refreshes the pack's `uv.lock`, which needs access to a package index.
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

- Inputs have `type` (`str`, `int`, `bool`, `float`, `list`, `dict`),
  `default`, `required`, `description`, `sensitive`, `scope` (`global` or
  `target`) and `from` (derivation templates).
- Templates render `{{ input }}` tokens and fail on unknown ones;
  `unknown_tokens: keep` leaves foreign tokens such as GitHub Actions
  expressions alone. Path fields render tokens too.
- `globs` has no implicit excludes: add `exclude: [".git/**"]` when the
  targets are Git clones.
- `transform` works on UTF-8 text only; `copy` and `remove` also handle
  binary files, which `--preview diff` shows as `Binary file PATH differs`.
- All paths must be relative and stay inside the target; `..` and symlinks
  out are rejected.
- Hook `args` are passed to the hook as written. The built-in `yaml_edit`
  renders `{{ input }}` tokens in its string values; your own hooks call
  `helpers.render_template()` when they need that.

### Hooks

A hook module exports `transform()`, `validate()`, or both. `init hook`
writes a typed stub and a pytest for it. Debug one hook without a recipe:

```bash
untaped recipe hook run acme/pin_python --target ~/work/api --file pyproject.toml --diff
```

Validate hooks return `helpers.pass_()`, `helpers.fail(msg)` or
`helpers.skip(msg)`; a skipped target is not a failure. `helpers.warn(msg)`
adds a warning to the target. Hooks must only read the target and their own
pack: no writes, no network.

For YAML files use the built-in `yaml_edit` hook shown above. Its
`args.edits` list takes `op: set|merge|delete|ensure`, a `path` of mapping
keys, `{index: N}` or `{where: {...}}` selectors, and a `value`. A file whose
content would not change is left byte-identical.

### Golden tests

`init recipe` also creates a test case under `tests/RECIPE/CASE/`: `given/`
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
untaped recipe backup list
untaped recipe backup get latest
untaped recipe backup restore latest --dry-run
untaped recipe backup restore latest
untaped recipe backup prune --keep 20
```

`restore` refuses to overwrite a file that changed after the backup unless
you pass `--force`. Backups hold file content only, not modes or times.
`prune` falls back to `recipe.backup_keep` and `recipe.backup_max_age_days`.

## Output

`apply` prints one `recipe.apply_outcome` row per target with `action`:
`planned`, `applied`, `unchanged`, `skipped`, `cancelled` or `failed`. See
[Pipes and record kinds](../reference/pipes.md#recipe) for the other
commands.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `recipe.library_root` | `~/.untaped/untaped-recipes` | Installed packs. |
| `recipe.hook_timeout_seconds` | `60` | Per-hook timeout; `0` disables. `apply --hook-timeout` overrides it. |
| `recipe.hook_startup_timeout_seconds` | `300` | Time allowed to prepare a hook environment. |
| `recipe.preview_max_rows` | `50` | Preview rows before per-file rows collapse; `0` is unlimited. |
| `recipe.backup_keep`, `recipe.backup_max_age_days` | unset | Defaults for `backup prune`. |

## See also

- [Workspaces](../workspace/usage.md)
- [Exit codes](../reference/exit-codes.md)
- [Configuration reference](../reference/config.md#recipe)
