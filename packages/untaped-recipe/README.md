# untaped-recipe

Install it as part of `untaped`: `uv tool install 'untaped[recipe]'` or `pip install 'untaped[recipe]'`.
To add it to an existing install, see [Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install).

`untaped recipe` applies the same file changes to many directories: add a
config file, bump a version in YAML, remove a stale workflow. A *recipe* is a
list of steps; *packs* bundle recipes with Python *hooks*. Every change is
planned in memory, previewed, and written only after you confirm, with a
backup of every file it touches. Recipes never commit, push or open pull
requests.

## Set up

Install a pack (see below). `uv` must be on your `PATH` to run pack hooks:

```bash
uv --version
untaped recipe list
```

Where packs live, hook timeouts and backup retention are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#recipe).

## Apply a recipe

```bash
untaped recipe get acme/editorconfig
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --var python_version=3.14 --dry-run
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --preview diff
untaped recipe apply acme/editorconfig ~/work/api ~/work/web --var python_version=3.14
```

You get a preview of every file each target would gain, change or lose, then
a confirmation; each target is written as one transaction. Pack hooks run
even on `--dry-run`, because they compute the plan.

## Check for drift in CI

```bash
untaped recipe apply acme/editorconfig ~/work/api --check
```

`--check` writes nothing, asks nothing and exits `3` when any target would
change.

## Apply to every repo of a workspace

```bash
untaped workspace status PROJ-123 --format pipe \
  | untaped recipe apply acme/editorconfig --stdin --dry-run
```

`--stdin` takes target paths, or records that carry a `target_path`, so a
[workspace](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/README.md)'s
records choose the targets.

## Install and manage packs

```bash
untaped recipe packs add https://git.example.com/acme/untaped-recipes.git --rev v1.2.0
untaped recipe validate acme
untaped recipe test acme
untaped recipe packs sync --all --dry-run
```

Installing a pack installs code: its hooks run as you, with no sandbox.
Inspect a pack with `packs get` and `validate` (which never imports hooks)
before you trust it; its golden tests run hooks, so run them after. Also
review what `packs sync` lists as you would a dependency upgrade.

## Write a pack

```bash
untaped recipe packs init acme
untaped recipe init ./acme/editorconfig
untaped recipe hooks init ./acme/pin_python --kind transform
untaped recipe test ./acme
```

A pack is a Python project of YAML recipes, hooks and golden test cases;
each `init` scaffolds one piece and refreshes the pack's `uv.lock`.

## Undo an apply

```bash
untaped recipe backups list
untaped recipe backups restore latest --dry-run
untaped recipe backups restore latest
```

Each apply writes one backup bundle; `restore` puts the whole bundle back
after a preview.

## Reference

The [packaged skill](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-recipe/src/untaped_recipe/skills/untaped-recipe/SKILL.md) is the full reference: every workflow, safety rule and pitfall. Install it for your agent with `untaped skills install recipe --target claude` (or another [agent](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md#install-skills)); `untaped recipe COMMAND --help` lists each command's options.

- [Output records](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#recipe) and [exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes)
- [Settings](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#recipe)
