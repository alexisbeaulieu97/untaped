# Authoring recipe packs

Contents: scaffolding, recipe files, templates and paths, hooks, debugging a
hook, the built-in `yaml_edit` hook.

## Scaffolding

- `packs init NAME`, `init PACK/RECIPE` and `hooks init PACK/HOOK` scaffold
  a pack, a recipe (with a starter golden case) and a hook. A local path such
  as `hooks init ./my-pack/probe` targets `./my-pack`.
- `hooks init` writes a typed stub and a direct-call pytest. Packs ship
  `pytest` with `pythonpath = ["src"]`, so `uv run --project PACK pytest`
  works at once. `--kind X --force` replaces both files; without `--force`
  an existing hook is refused.
- Scaffolding refreshes the pack's `uv.lock`, which needs package-index
  access (or a `[tool.uv.sources]` override). If locking fails, the files
  stay and the error says how to repair them.
- `--no-lock` skips locking, but hooks cannot run until `uv lock` succeeds:
  workers run with `uv run --locked --no-dev`.
- A pack's `pyproject.toml` lists recipes under
  `[tool.untaped_recipe.recipes]` and hooks under
  `[tool.untaped_recipe.hooks]`. Keep `untaped[recipe]` a dev-only dependency; put
  hook runtime dependencies in `[project].dependencies`.

## Recipe files

- A recipe holds `version: 1`, optional `description`, optional `inputs`,
  and `steps`; `name:` is rejected.
- Each input takes `type` (`str`, `int`, `bool`, `float`, `list`, `dict`),
  `default`, `required`, `description`, `sensitive`, `scope` (`global` or
  `target`) and `from` (derivation templates).
- A `default` must coerce to the input's `type` (`validate` reports it) and
  cannot be combined with `required: true`.
- Step types and their required fields: `validate` (`hook`), `transform`
  (`hook` and one of `file`/`files`/`globs`), `template` (`template`, `dest`),
  `copy` (`source`, `dest`), `remove` (one of `file`/`files`/`globs`). Hook
  steps pass `args`. Use YAML anchors to reuse structure.

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

| Field | Steps | Meaning |
|---|---|---|
| `file` / `files` / `globs` | `transform`, `remove` (exactly one) | one file, a list expanded at load, or patterns matched while planning |
| `exclude` | with `globs` | patterns to skip; there are no implicit excludes, so Git targets need `exclude: [".git/**"]` |
| `optional: true` | `transform` with `file`/`files` | skip a missing file with a warning |
| `if_absent: true` | `template`, `copy` | create only when the destination does not exist |
| `unknown_tokens: keep` | `template` | keep foreign `{{ }}` tokens (GitHub Actions, Helm) |

- `transform` rejects binary files, so exclude them. `copy` and `remove`
  handle binary files byte for byte, and `--preview diff` prints
  `Binary file PATH differs` for them.
- Glob matches under symlinked directories are skipped with a warning.

## Templates and paths

- Template bodies render `{{ name }}` tokens from inputs and fail on an
  unknown token unless `unknown_tokens: keep` is set.
- Path fields (`template`, `dest`, `source`, `file`, `files`, `globs`,
  `exclude`) also render tokens, always strictly. Sensitive and structured
  inputs are forbidden there; derive a scalar input with `from` instead.
- Every recipe-local and target-relative path must be relative and safe:
  absolute paths, `..` segments and symlink traversal are rejected before
  any read or write, and again after rendering.

## Hooks

- A hook module exports `transform()`, `validate()`, or both; the exported
  name is the contract, and manifest rows declare only `module`.
- Hook `args` pass verbatim. Hooks read resolved `inputs` natively (lists and
  dicts stay structured) and call `helpers.render_template()` for templated
  string args.
- A bare hook name in a pack's recipe resolves to the pack's own hook, else a
  built-in, never another pack; write `pack/hook` for that. Only recipes
  outside a pack, and `hooks run` without `--project`, look bare names up
  across installed packs.
- Hooks stay pure while planning: read only the target tree and their own
  pack, never write or reach the network. They run on every dry run.
- Validate verdicts: `helpers.pass_()`, `helpers.fail(msg)` and
  `helpers.skip(msg)` (the recipe does not apply; the target is `skipped`,
  not failed). Returning `None` passes; a plain string fails with that
  message.
- `helpers.warn(msg)` adds a warning to the target, any number of times, from
  validate and transform hooks; the hook still returns its verdict.

## Debug a hook

`hooks run REF --target DIR` runs one hook without a recipe:

- a transform needs `--file` and prints the transformed content, or a diff
  with `--diff`;
- a validate emits a `recipe.hook_run` verdict and exits 1 only on `fail`;
- a hook that raises prints its traceback as an `error:` and exits 1.

Supply fixture content with `--content`/`--content-file`, inputs with
`--var`/`--vars-file`, and args with `--arg`/`--args-file`. Values are
YAML-parsed, so quote strings such as `'v="3.10"'`.

A local hook project runs only when named, with `--project PATH` or a
`./pack/hook` ref; running inside a cloned repo never executes its hooks.
Context (fixture values included) and warnings go to stderr, so use `--quiet`
when values are sensitive.

## The built-in `yaml_edit` hook

- `edits` is a list of `op: set|merge|delete|ensure` with a `path` of
  mapping keys, `{index: N}` or first-match `{where: {...}}` selectors.
- `ensure` adds a value only when absent: list membership by `match` keys or
  equality, or set-if-absent for mappings.
- String values render `{{ input }}` tokens and honour args-level
  `unknown_tokens: keep`.
- A file whose content would not change stays byte-identical.
