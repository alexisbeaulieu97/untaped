# Deprecating something

A deprecation is a promise with a date: the thing keeps working, warns, and
goes in the major release its declaration names. You write the date once, on
the mark, as `removed_in=<major>`; the warning, the `--help` line, `config
list`, `doctor`, the public surface (`surface.txt`) and the release checks
all read it from there ([Versioning](../versioning.md) says what stays
stable until then). Never type the sentence yourself: `untaped lint` reports
a hand-typed one as `lint.hand-typed-deprecation`.

## The kinds

| Thing | Declaration |
|---|---|
| A command, group or plugin | `@deprecated(removed_in=3, replacement=set_command)` on the command, `create_app(..., stability=deprecated(removed_in=3))` on a group, `PluginSpec(..., stability=deprecated(removed_in=3))` on a plugin |
| A renamed command or flag | `deprecated_alias(parent_app, "me", "whoami", removed_in=3)`; a flag: `deprecated_alias(command_app, "--old", "--new", removed_in=3)` |
| A flag with no new spelling | `dry_run: Annotated[bool, Parameter(group=DEPRECATED_OPTIONS), deprecated(removed_in=3, replacement="--limit 1 --yes")] = False` on the command |
| A setting | `old: Annotated[bool, deprecated(removed_in=3, replacement="acme.mode")] = False` |
| A renamed setting key | `renamed_keys = {"old_key": Renamed("new_key", removed_in=3)}` on the section's model |
| A cache setting's old key | `Cache(..., renamed_from=("stale_after_seconds",), removed_in=3)` |
| A function or method of your Python API | `@deprecated(removed_in=3, replacement=new_api)` over `@warnings.deprecated("<its sentence>")`, under any `@classmethod`, `@staticmethod` or `@property` |
| A parameter of your Python API | `kind: Annotated[str \| None, deprecated(removed_in=3, replacement="kind= on the Record class")] = None` on a function under `@deprecated_parameters`, itself under any `@classmethod` or `@staticmethod` |
| A behaviour | `ROW_KIND = deprecated_behaviour("row-kind", removed_in=3, replacement="kind= on the Record class")`, listed with `PluginSpec(..., deprecations=lambda: (ROW_KIND,))` |

`removed_in` is a bare major of your distribution: `3` for `untaped-acme
3.0`. untaped's own plugins use untaped's majors, since they release in
lockstep. A mark without `removed_in` still works and warns "a future major
release", but nothing can hold it to a date: `untaped lint` reports it
(`lint.undated-deprecation`) and `untaped.sdk.testing.assert_surface` refuses it
when you pass your `version=`.

A behaviour has no object to mark, so it gets a name, and listing it on your
spec gives it a `behaviour acme.row-kind` surface line (keep the import inside
the function, as for `contracts`). Where the behaviour happens, name what goes
and let it build the sentence: `ROW_KIND.notify("a row without a declared kind",
key=...)` warns once per `key` on the command's stderr, `ROW_KIND.warn(...)`
raises an `UntapedDeprecationWarning` from a Python API, and
`ROW_KIND.sentence(...)` returns the text for a row of your own. Each takes
`replacement=` for one site, `None` for none. A behaviour no spec lists reads
as untaped's own, so list every one.

A Python API callable carries two marks because each has its reader: untaped's
dates it on the surface, and the `warnings.deprecated` literal is the only
form type checkers report, so it must be a plain string. `untaped lint` checks
they agree and prints the literal to paste (`lint.deprecation-text`); it also
reports either one alone and a mark on an `@overload` item. A marked parameter
warns through `@deprecated_parameters` whenever it is passed, its default
value included, since the major that removes it makes any spelling of it an
error (`lint.unwarned-parameter` reports a function without the decorator).

A deprecated flag sits in `DEPRECATED_OPTIONS`, a panel `--help` lists only
under `--deprecated`, where its help is its `Deprecated:` line; core puts it
there from the mark on its field. Only an option can carry the mark:
composition refuses it on an argument (`deprecated-argument`). Any spelling of the flag warns (`--dry-run`,
`--no-dry-run`, `--dry-run=false`, a short flag), up to a `--`.

The replacement is what to use instead: a command's function or app (the
path follows a rename), or text such as a command (`"untaped acme set"`), a
setting key or prose; on a Python API, a function or class reads as its name
(`` `new_api` ``). An alias and a renamed key name their new spelling
themselves.

## What users see

```console
$ untaped acme put
warning: `untaped acme put` is deprecated and will be removed in untaped-acme 3.0; use `untaped acme set`
$ untaped acme put --help
...
Deprecated: removed in untaped-acme 3.0; use untaped acme set.
```

Each warning prints once per run, survives `-q`, and under `--format json`
is an `untaped.deprecation` line carrying the thing's `id` (its surface
line's identity, or `sdk <function>(<parameter>=)` for a parameter),
`removed_in`, `distribution` and `replacement`
([Scripting](../scripting.md#stderr-diagnostics)). A Python API's
`UntapedDeprecationWarning` (a behaviour's `warn`, a marked parameter) that
Python shows (under `-X dev`, or a filter that shows it) arrives as the same
line while a command runs; a deprecated function's `warnings.deprecated`
arrives as a plain warning line.
untaped's own deprecations name the version without a distribution.
`config list` notes "use acme.mode, removed in untaped-acme 3.0" on a
deprecated setting and "from acme.old_key, removed in untaped-acme 3.0" on a
value read through an old key; `doctor` names the version beside each old key.

## The checks

In this workspace `tests/repo/test_major_release.py` reads the marks of
`tests/repo/surface.txt`:

```text
cli untaped acme sync  [deprecated, removed in 12.0; use untaped acme pull]
```

- nothing may still be marked removed in the major main carries or earlier
  (from `12.0.0rc1` on, that is 12), so whatever 12.0 removes goes in prep PRs
  before the release PR; `uv run python scripts/gen_surface.py --due [MAJOR]`
  lists those lines and exits 1 while there are any;
- every deprecated line names its removal version;
- against the last final release, a date it showed never falls, and its mark
  never goes while the thing stays.

`tests/repo/test_surface.py` lets a deprecated line go only at its major,
while that major's prep (an `upgrading` fragment) is open. A plugin in its
own repository gets the first two checks from
`assert_surface(SPEC, path, version=__version__)`.

## Postponing

Raise `removed_in` on the declaration, regenerate the surface
(`uv run python scripts/gen_surface.py`) and add a
`changelog.d/<slug>.deprecated.md` fragment saying why. The release build
writes the fact itself under Deprecated, unless an entry of that release
names the thing:

```markdown
- `untaped.sdk.old_helper` will now be removed in 14.0 instead of 13.0.
```

A date users have seen in a release never moves earlier.
