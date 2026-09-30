# Authoring recipe packs

- `packs init <name>`, `init <pack>/<recipe>`, `hooks init <pack>/<hook>`
  scaffold pack projects; explicit local paths like `hooks init ./my-pack/probe`
  target `./my-pack`. `init` also scaffolds a starter golden case;
  `hooks init` writes a typed stub plus a direct-call pytest (naming the kind and
  `--kind` on success), and packs ship `pytest` with `pythonpath = ["src"]` so
  `uv run --project <pack> pytest` works immediately. `hooks init --kind X --force`
  replaces both the stub and the paired pytest (e.g. to fix a wrong `--kind`);
  without `--force` an existing hook is refused.
- Scaffolding refreshes the pack `uv.lock` and needs package-index access (or a
  `[tool.uv.sources]` override). If `uv lock` fails after files are written,
  the scaffold stays in place with a repairable error; `--no-lock` skips
  locking, but hooks cannot run until `uv lock` succeeds because workers use
  `uv run --locked --no-dev`.
- A pack's `pyproject.toml` lists its recipes under
  `[tool.untaped_recipe.recipes]` and its hooks under
  `[tool.untaped_recipe.hooks]`.
- Recipe YAML is behavior-only: `version: 1`, optional `description`, optional
  `inputs`, and `steps`; `name:` is rejected. Each input takes `type` (`str`,
  `int`, `bool`, `float`, `list`, `dict`), `default`, `required`,
  `description`, `sensitive`, `scope` (`global` or `target`) and `from`
  (derivation templates). Step types are `validate`,
  `transform`, `template`, `copy`, and `remove`. `transform`/`remove` take
  exactly one of `file`, `files` (load-time fan-out to per-file steps), or
  `globs` (planning-time discovery; `exclude` skips matches; no implicit
  excludes, so repo sweeps usually add `exclude: [".git/**"]`; `transform`
  rejects binary files, so exclude them; `copy` and `remove` handle binary
  files byte-exact, and `--preview diff` prints `Binary file PATH differs`
  for them; matches under symlinked directories are skipped with a
  warning). `optional: true` (transform with `file`/`files` only)
  skips missing files with a warning. `template`/`copy` accept
  `if_absent: true` to create only when the destination does not exist.
- Template bodies render `{{ name }}` tokens from inputs, strict by default;
  `unknown_tokens: keep` preserves foreign tokens (GitHub Actions, Helm) while
  still rendering known inputs. Path-bearing fields (template/dest, source,
  file/files/globs/exclude) also render bare tokens — always strict, re-checked
  as confined relative paths after rendering. Sensitive and structured inputs
  are forbidden in path fields; derive a scalar input with `from` instead.
- Hook `args` pass verbatim — the engine never templates them; hooks read
  resolved `inputs` natively (structured inputs as real lists/dicts) and call
  `helpers.render_template()` themselves for templated string args. Use YAML
  anchors for structural reuse in recipes.
- A hook module exports `transform()`, `validate()`, or both — the exported
  name is the contract; manifest rows declare only `module`. Keep `untaped`
  as a dev-only dependency (scaffolding pins `>=<installed>,<next major>`);
  runtime hook dependencies go in `[project].dependencies`.
- Hook refs in a pack's recipes: a bare name resolves to the pack's own hook,
  else a built-in — never to another installed pack. Reference another pack's
  hook as `pack/hook`. (Only recipes without a project, and `hooks run` without
  `--project`, look bare names up across installed packs.)
  Hooks must stay pure at planning time: read only the target tree and their
  own pack, never write or reach the network.
- Validate verdicts are `helpers.pass_()`, `helpers.fail(msg)`, and
  `helpers.skip(msg)` (not applicable → target `skipped`, never a failure).
  `helpers.warn(msg)` is a warning accumulator callable any number of times from
  validate and transform hooks; warnings attach to the target plan and do not
  replace the verdict. Call it for its side effect, then return a pass, fail, or
  skip verdict. `None` remains an implicit pass and a plain string is a fail
  message; unknown verdict objects and status values are rejected.
- `hooks run <ref> --target DIR` debugs one hook without a recipe: transforms
  need `--file` (stdout is exact transformed content, or `--diff`); validates
  emit a `recipe.hook_run` verdict (`pass`/`fail`/`skip`) and exit 1 only
  on `fail`; a hook that raises prints its traceback as an `error:` and exits 1. The ref accepts the `./pack/hook` path form (resolves as
  `--project ./pack` + hook name; combining with explicit `--project` is a usage
  error). A local hook project is used only when named with `--project PATH`
  or a `./path` ref — never adopted implicitly from the current directory, so
  running inside a cloned repo does not execute its hooks.
  `--content`/`--content-file` supply fixture content; hook inputs come from
  repeated `--vars-file`/`--var KEY=YAML` and hook args from repeated
  `--args-file`/`--arg KEY=YAML` (later files win, flags win over files;
  values are YAML-parsed, so quote strings such as `'v="3.10"'`). Context echo (including fixture values) and
  accumulated warnings go to stderr — use `--quiet` in shared terminals when
  values are sensitive.
- For common YAML edits use the built-in `yaml_edit` transform hook: `edits`
  with `op: set|merge|delete|ensure`, paths of mapping keys, `{index: N}`, or
  first-match `{where: {...}}` selectors; string values render `{{ input }}`
  tokens and honor args-level `unknown_tokens: keep`. `ensure` idempotently adds
  a value if absent (list membership by `match` keys / equality, or mapping
  set-if-absent). Every op leaves the file byte-identical when nothing
  changes (`set`/`merge` to the value already present are no-ops).
