---
description: "Read before writing or editing a recipe file: its inputs, step types and fields, templates, and which paths are allowed."
---

## A recipe file

A recipe holds `version: 1`, an optional `description`, optional `inputs`
and `steps`; a `name:` key is rejected. YAML anchors reuse structure.

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

## Inputs

Each input takes `type` (`str`, `int`, `bool`, `float`, `list`, `dict`),
`default`, `required`, `description`, `sensitive`, `scope` (`global` or
`target`) and `from` (templates that derive a value per target). A `list`
input may set `items`, and a `dict` input `values`, to the scalar type
(`str`, `int`, `bool`, `float`; default `str`) its elements are coerced to;
neither is valid on another type. A `default` must coerce to the input's
`type` (`validate` reports it) and can't be combined with `required: true`.
How `apply` fills them: `untaped agents recipe inputs`.

## Steps

| Type | Required fields |
|---|---|
| `validate` | `hook` |
| `transform` | `hook`, and one of `file`/`files`/`globs` |
| `template` | `template`, `dest` |
| `copy` | `source`, `dest` |
| `remove` | one of `file`/`files`/`globs` |

Hook steps pass `args`.

| Field | Steps | Meaning |
|---|---|---|
| `file` / `files` / `globs` | `transform`, `remove` (exactly one) | one file, a list expanded at load, or patterns matched while planning |
| `exclude` | with `globs` | patterns to skip; there are no implicit excludes, so Git targets need `exclude: [".git/**"]` |
| `optional: true` | `transform` with `file`/`files` | skip a missing file with a warning |
| `if_absent: true` | `template`, `copy` | create only when the destination doesn't exist |
| `unknown_tokens: keep` | `template` | keep foreign `{{ }}` tokens (GitHub Actions, Helm) |

`transform` rejects binary files, so exclude them. `copy` and `remove`
handle binary files byte for byte, and `apply --preview diff` prints
`Binary file PATH differs` for them. Glob matches under symlinked
directories are skipped with a warning.

## Templates and paths

Template bodies render `{{ name }}` tokens from inputs and fail on an
unknown token unless `unknown_tokens: keep` is set. Path fields
(`template`, `dest`, `source`, `file`, `files`, `globs`, `exclude`) also
render tokens, always strictly. Sensitive and structured inputs are
forbidden in paths; derive a scalar input with `from` instead.

Every recipe-local and target-relative path must be relative and safe:
absolute paths, `..` segments and symlink traversal are rejected before any
read or write, and again after rendering.
