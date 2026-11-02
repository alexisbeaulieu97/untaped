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
| A command, group or plugin | `stability = deprecated(removed_in=3, replacement=Set)` on its class |
| A renamed command | `aliases = (Deprecated(replacement="me", removed_in=3),)` on the renamed command's class, naming its old spelling |
| A flag | `dry_run: Annotated[bool, deprecated(removed_in=3, replacement="--limit 1 --yes")] = Field(False, description="...")` on the command |
| A setting | `old: Annotated[bool, deprecated(removed_in=3, replacement="acme.mode")] = False` |
| A renamed setting key | `renamed_keys = {"old_key": Renamed("new_key", removed_in=3)}` on the section's model |
| A cache setting's old key | `Cache(..., renamed_from=("stale_after_seconds",), removed_in=3)` |
| A function or method of your Python API | `@deprecated(removed_in=3, replacement=new_api)` over `@warnings.deprecated("<its sentence>", category=UntapedDeprecationWarning)`, under any `@classmethod`, `@staticmethod` or `@property` |
| A parameter of your Python API | `kind: Annotated[str \| None, deprecated(removed_in=3, replacement="records on the plugin class")] = None` on a function under `@deprecated_parameters`, itself under any `@classmethod` or `@staticmethod` |
| A renamed contract method | the old method stays on the contract with both marks of a method, `@deprecated(removed_in=3, replacement=new_method)` first; a provider still filling the old name fills the new one, and `gather` or `Stdin(via=)` given the old name asks the new one |
| A renamed name of `untaped.sdk` or a group (core) | `size_words=deprecated(removed_in=14, replacement=size_text)` in `old_names(...)`: the root's `OLD_NAMES` in `sdk/_exports.py`, or the group's `lazy_names(..., old=...)`; plus `size_words = size_text` in the module's `TYPE_CHECKING` block |
| A field of a model in your Python API | `old: Annotated[int, deprecated(removed_in=4, replacement="name")] = 0` on a model with `extra="forbid"` or `"allow"` |
| A behaviour | `row_kind = DeprecatedBehaviour(removed_in=3, replacement="records on the plugin class")` on the plugin class |

`removed_in` is a bare major of your distribution: `3` for `untaped-acme
3.0`. untaped's own plugins use untaped's majors, since they release in
lockstep. A mark without `removed_in` still works and warns "a future major
release", but nothing can hold it to a date: `untaped lint` reports it
(`lint.undated-deprecation`) and `untaped.sdk.testing.assert_surface` refuses it
when you pass your `version=`.

A name plugins use (your `api` and `testing` modules, your contracts) lasts
two majors after its deprecation, so a plugin built on one major keeps
working on the next: deprecated in `untaped-acme 2.x`, it names
`removed_in=4` at the earliest. It goes only at that major, a parameter only
with its function, and a changed signature, default or return type is a new
name beside the old one ([Versioning](../versioning.md#names-plugins-use)).

A behaviour has no object to mark, so the attribute holding it names it:
`row_kind` on `class Acme(Plugin)` is the surface line `behaviour
acme.row-kind`. Where the behaviour happens, name what goes and let it build
the sentence: `Acme.row_kind.notify("a row without a declared kind",
key=...)` warns once per `key` on the command's stderr,
`Acme.row_kind.warn(...)` raises an `UntapedDeprecationWarning` from a Python
API, and `Acme.row_kind.sentence(...)` returns the text for a row of your
own. Each takes `replacement=` for one site, `None` for none.

A Python API callable carries two marks because each has its reader: untaped's
dates it on the surface, and the `warnings.deprecated` literal is the only
form type checkers report, so it must be a plain string. Its category is
`UntapedDeprecationWarning`, which untaped shows while a command runs and
reads its date and replacement from the mark over it. `untaped lint` checks
they agree and prints the decorator to paste (`lint.deprecation-text`); it also
reports either one alone and a mark on an `@overload` item. A marked parameter
warns through `@deprecated_parameters` whenever it is passed, its default
value included, since the major that removes it makes any spelling of it an
error (`lint.unwarned-parameter` reports a function without the decorator).

A deprecated flag sits in a panel `--help` lists only under `--deprecated`,
where its help is its `Deprecated:` line; core puts it there from the mark on
its field. Only an option can carry the mark: composition refuses it on an
argument (`deprecated-argument`). Any spelling of the flag warns
(`--dry-run`, `--no-dry-run`, `--dry-run=false`, a short flag), up to a
`--`. A renamed command keeps its old spelling as an alias; a renamed flag
or group has no declaration yet, so file an issue when a rename needs one.

An old SDK name is the new object itself: every lookup resolves it again and
warns, it stays out of `__all__`, and the surface lists it under its old
spelling until it goes. Type checkers never see the module's `__getattr__`,
so the `TYPE_CHECKING` assignment is how they accept it (a strict checker
doesn't re-export `import new as old`); `tests/repo/test_old_names_typed.py`
keeps those assignments and the table equal. An old name spells a class or a
function; a renamed constant, or a renamed name of a plugin's own `api`
module, has no declaration yet.

The replacement is what to use instead: a command's function or app (the
path follows a rename), or text such as a command (`"untaped acme set"`), a
setting key or prose; on a Python API, a function or class reads as its name
(`` `new_api` ``). An alias and a renamed key name their new spelling
themselves; in a command's `aliases`, `Deprecated(replacement=...)` holds the
old spelling instead, the one that still works.

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
line's identity: `sdk untaped_acme.api.fetch(retries=)` for a parameter),
`removed_in`, `distribution` and `replacement`
([Scripting](../scripting.md#stderr-diagnostics)). A Python API's
`UntapedDeprecationWarning` (a deprecated function, a marked parameter, a
behaviour's `warn`) arrives as the same line while a command runs, though
Python hides deprecation warnings outside `__main__`: untaped shows its own
for the run unless a filter of yours (`-W`, `PYTHONWARNINGS`, a test
harness's) covers them, which then decides. A function's id names it by the
module plugins import it from (`sdk untaped_acme.api.fetch`).
untaped's own deprecations name the version without a distribution.
`config list` notes "use acme.mode, removed in untaped-acme 3.0" on a
deprecated setting and "from acme.old_key, removed in untaped-acme 3.0" on a
value read through an old key; `doctor` names the version beside each old key.
A plugin importing or filling a deprecated name warns once, when untaped first
reads it, with the line's `plugin` naming it, and `doctor` lists it as a
`plugin-uses` row ([Versioning](../versioning.md#plugins-across-releases)).

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
while that major's prep (an `upgrading` fragment) is open, and holds the
names plugins use to their two majors. Only a security fix removes one
early: an `EARLY_REMOVALS` entry with its reason and a
`changelog.d/<slug>.security.md` fragment. A plugin in its own repository
gets the same checks from `assert_surface(Acme, path, version=__version__)`,
which also drops a line from the file once it is due.

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
