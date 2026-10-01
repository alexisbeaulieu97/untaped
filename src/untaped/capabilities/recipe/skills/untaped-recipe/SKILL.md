---
name: untaped-recipe
description: Applies reusable file recipes (templated files, YAML edits, copies, removals) across many directories or repos through the `untaped recipe` command, with a preview, backups and a CI drift check, and authors and tests recipe packs. Use when the user mentions recipes or recipe packs, codemods, the same file change across many repos, or drift checks.
---

# untaped recipe

Every write should be one the user saw in a preview, made by pack code the
user chose to trust.

`apply` plans every change in memory, previews it on stderr, confirms, backs
up, then writes. Recipes edit plain directories; they never commit, push or
run shell steps. Pack hooks are Python that runs as the user, with full file
access, whenever a plan is computed.

## Commands

| Task | Command | Use when |
|---|---|---|
| Find a recipe | `untaped recipe list`, `untaped recipe get acme/editorconfig` | before applying, to read its inputs and steps |
| Preview a change | `untaped recipe apply acme/editorconfig ./api --dry-run` | always first |
| Read full diffs | `untaped recipe apply acme/editorconfig ./api --dry-run --preview diff` | the table is not enough, or it collapsed to per-target rows |
| Write | `untaped recipe apply acme/editorconfig ./api --yes` | the user approved that exact preview |
| Check drift in CI | `untaped recipe apply acme/editorconfig ./api --check` | nothing must be written or asked; exit 3 means drift |
| Undo an apply | `untaped recipe backups restore latest --dry-run` | a write turned out wrong; restore after the user approves |
| Install or update packs | `untaped recipe packs add PATH_OR_GIT_URL`, `untaped recipe packs sync acme --dry-run` | the user named a pack to trust; see [references/library.md](references/library.md) |
| Check a pack statically | `untaped recipe validate acme` | before trusting or after editing; imports no hook code |
| Write or test a pack | `untaped recipe packs init acme`, `untaped recipe test acme` | authoring; see [references/authoring.md](references/authoring.md) |

The recipe argument is a bare name (unique across installed packs), a
`pack/recipe` ref, or a path. A value is a path only when it is `.` or `..`,
starts with `./`, `../`, `/` or `~`, or ends in `.yml`/`.yaml`; anything else
is looked up in the library, never on disk. For a local pack directory, pass
its path and `--recipe NAME`.

## Workflows

### Apply a recipe safely

1. Read the recipe with `untaped recipe get REF`. Check that its pack is one
   the user installed on purpose: the next step already runs its hooks.
2. Collect every required input and pass it with `--var KEY=VALUE` or
   `--vars-file FILE`; add `--non-interactive` so a gap fails instead of
   waiting on a prompt.
3. Run `apply ... --dry-run` on the exact targets. Check that every target
   appears and that no row is `failed`.
4. Show the user the preview: the targets, the files per target, and any
   `warnings`. Mention targets whose detail was hidden because they resolve a
   sensitive input.
5. After the user approves, rerun the same command with `--yes` in place of
   `--dry-run`. Keep backups on unless the tree is protected another way.
6. Check the rows: `applied` or `unchanged` for each target and exit 0.
   A `failed` row wrote nothing for that target; the other targets still ran.

To apply across a workspace, pipe its repos and keep the same preview-first
order:

```text
untaped workspace repos list prod --format pipe \
  | untaped recipe apply acme/editorconfig --stdin --dry-run
```

### Check drift

`apply --check` writes nothing, creates no backups and never prompts. It exits
3 when any target would change (rows say `planned`) and 1 when any target
fails. A `skipped` target is neither drift nor failure.

## Safety

- `apply`, `packs sync`, `packs remove`, `backups restore` and
  `backups prune` preview and confirm. Run each with `--dry-run`, show the
  user what it lists, and pass `--yes` only after approval.
- Without a terminal, `apply --stdin` refuses before planning (exit 2) unless
  `--yes`, `--dry-run` or `--check` is given.
- Declining exits 1 with `cancelled; no changes made` and `cancelled` rows;
  a real failure also exits 1, so read the rows to tell them apart.
- Exit codes: 0 success, 1 failure or declined, 2 usage error, 3 `--check`
  drift, 4 fix the environment (`uv` missing, `$EDITOR`, settings), 5 retry
  later (a git fetch timeout), 130 interrupted. A run exits with its most
  severe failure.
- `backups restore` refuses to overwrite files changed after the backup;
  `--force` overrides that, so ask first.

## Output

Read rows with `--format json`, or `--format pipe` to chain into other
untaped commands; stdout carries rows only, and the preview goes to stderr.
Each `apply` row has the target's absolute `target_path` and an `action`:
`planned` (a dry run or check would change it), `applied`, `unchanged`,
`skipped` (a validate hook said the recipe does not apply; a success),
`cancelled` or `failed` (with `detail` and `error`).

## Pitfalls

- `--dry-run` is not a way to inspect an untrusted pack: hooks run to compute
  the plan. Inspect with `packs get`, `validate` and `test` instead.
- `--vars-file` values are YAML: `3.10` becomes `3.1`. Quote version-like
  strings.

## References

| File | Read it when |
|---|---|
| [references/apply.md](references/apply.md) | choosing a preview style, running in parallel, deriving inputs per target, prompting rules, sensitive inputs, or how piped records become targets |
| [references/library.md](references/library.md) | installing, syncing or removing packs, running `validate` or golden `test` cases, restoring or pruning backups, or judging what a hook can reach |
| [references/authoring.md](references/authoring.md) | scaffolding a pack, writing recipe YAML or hooks, debugging with `hooks run`, or using the built-in `yaml_edit` hook |
