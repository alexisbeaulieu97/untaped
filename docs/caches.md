# Caches

A cache is data a plugin keeps from an upstream it can ask again (an index
built from GitHub, a listing), with a max age after which it is checked
before use. Declare one `Cache` per thing your plugin keeps, beside your
settings, and serve every read through it rather than writing your own
freshness check, `--refresh` flag or stale warning:

```python
from datetime import timedelta
from typing import Annotated

from untaped.sdk import Cache, echo, refresh_option, report_errors

MOTD = Cache("acme", "motd", label="message of the day", max_age=timedelta(hours=1))


@app.command(name="motd")
def motd(*, refresh: Annotated[bool | None, refresh_option(MOTD)] = None) -> None:
    """Print the message of the day."""
    with report_errors():
        served = MOTD.serve({"motd": "the message of the day"}, refresh=refresh,
                            fill=fetch_motd, read=read_motd)
    echo(served.value)
```

List each cache on your spec, `PluginSpec(..., caches=(MOTD,))`: that
gives it the setting `acme.caches.motd.max_age_seconds`, defaulting to the
declared `max_age` (in whole seconds; `0` checks every time), listed with your
other settings in `config list`, and read by `serve()` on every call. A cache
left off the spec always uses its declared default. Composition quarantines a
plugin that lists another plugin's cache (`foreign-cache`) or the same name
twice (`duplicate-cache`).

`serve(keys, refresh=, fill=, read=)` takes the keys the command reads,
each with a label for a human (`{"prod": "source prod"}`). Per key, it
refreshes when the directive asks: `None` (the default) when the key is older
than its max age, `--refresh` now, `--cached` never. `fill(key, handle)`
fetches one key into your own store and returns a `Filled`, reporting
progress through `handle`; `read()` then computes the command's answer once,
over every key. `serve()` opens the refresh's progress task, holds the key's
lock while it fills (another process filling the same key is waited for,
then served), and keeps what it knows about each key in a file under
`plugin_dir(SPEC)/caches/`, per profile: keep your own store apart the same
way (by key, and by profile when a setting can change what a key holds).
A fill never serves its own cache; serving another one, it passes its
`handle` as `serve(progress=)`, so the inner refresh shows as its phase.

An automatic refresh of cached data fails fast: until one call succeeds, every
HTTP request and networked `git` command (`clone`, `fetch`, `ls-remote`,
`pull`) inside the fill is tried once, with a short timeout, so an offline
user gets the cached data in seconds; `--refresh` and a key's first fill keep
the full timeouts and retries. When a fill raises an `UntapedError`, `serve()`
serves the cached data with one `untaped.stale` warning and does not try again
for five minutes, or until the time the fill put in the error's
`details["retry_at"]` (ISO 8601; a rate limit's reset). With nothing cached it
raises the error, and the next run fills again. A fill that completed but left
items out returns `Filled(partial=Failure(...))`: every read warns until a
whole refresh. Pass `empty=` to say when an answer found nothing: a stale
empty answer then fails with the refresh's error instead of passing for an
absence. `--cached` on a key never filled raises `NeverRefreshed`.

A cache's max age is never a settings field of your own
(`tests/repo/test_cache_setting.py` checks first-party plugins). To move one
into the generated setting, name its old keys in
`Cache(..., renamed_from=("stale_after_seconds",))` (dotted paths relative to
your section, never under `caches`): each is still read, with a warning, and
`config migrate` moves it, as with `renamed_keys`. Delete the field from your
model in the same change. An older key of yours that renamed to it becomes
`Retired(note="use acme.caches.motd.max_age_seconds")` in `retired_keys`: your
own declarations never point at the generated setting. Composition
quarantines an old key the model still has as a setting, or that two caches
or your own declarations also name (`bad-settings-keys`).

Two more rules keep every cache alike. Declare the directive as
`Annotated[bool | None, refresh_option(CACHE)] = None`, never a hand-made
`--refresh` or `--cached`: its help names the cache's setting and default
(`tests/repo/test_refresh_option.py` checks first-party code). And never
print a stale warning yourself: `serve()` says it once per key. Inside a
provider method that `gather` asked, the warning waits until the answer is
used, and is never said if it isn't
([contracts](./contracts.md#how-providers-are-loaded-and-chosen)).

## When the code that fills a cache changes

A cache's data can go out of date without upstream changing: a new release of
your plugin parses files differently, or a library it relies on does. Name the
version of the code that fills the cache with `version=`, a function
called each time the cache is read (never at import):

```python
from untaped.sdk import Cache, code_version

INDEX = Cache(
    "acme",
    "index",
    label="source data",
    max_age=timedelta(days=1),
    version=lambda: code_version("acme.domain.parser", distributions=("PyYAML",)),
)
```

`code_version(module, distributions=, extra=)` hashes the bytes of the
module's file (found without importing it), the installed version of each
distribution, and each `extra` string, once per process: editing the parser
or upgrading PyYAML changes it, and nothing needs bumping by hand. It reads
that one file, so name the libraries it relies on, and keep first-party code
that shapes the data in that module or hash its modules too
(`extra=(code_version("acme.domain.identity"),)`). A `version` that raises
fails every read of the cache, `--cached` included.

The sidecar keeps the version each key was filled at. A key filled at
another version is *outdated*: due the way a key past its max age is (auto
refreshes it, its data is kept, and after a failed refresh the retry window
holds), and stale: `--cached` serves it with one `untaped.stale` warning whose
`outdated` is true (`source prod: made by another version of the acme plugin;
using data from …`). `seed()` writes no version, so data seeded from an older
release is refreshed on its first read. Two installs at different versions
that share a profile (a release and a development checkout) refill each
other's keys on every read. Without `version=` nothing changes.

## Stores

Two installs of untaped can share one `~/.untaped`: a tool and a development
checkout, or one-off runs of other versions. A file your plugin keeps under
`plugin_dir(SPEC)` is shared by all of them, so it must be readable by every
release of the current major, or be a *store*: a file or folder kept apart per
layout. A layout is the oldest layout of the data your reader still opens.
Bump it for a change an older reader cannot open (a column that changes
meaning, a dropped table); new tables or columns an older reader ignores do
not bump it, and a change of content is `version=`, not a layout.

```python
from untaped.sdk import Store, plugin_dir

CATALOGUE = Store("acme", "catalogue", base=lambda: plugin_dir(SPEC) / "catalogue.sqlite3", layout=2)
SPEC = PluginSpec(name="acme", ..., stores=(CATALOGUE,))

def connect() -> sqlite3.Connection:
    return sqlite3.connect(CATALOGUE.open())     # catalogue.v2.sqlite3
```

`base` returns the path every layout is named beside: layout `N` is
`<stem>.vN<suffix>` (`catalogue.v2.sqlite3`; a folder `catalogue` is
`catalogue.v2`), the first layout included, so two releases at different
layouts never open each other's file. `path()` names this layout and touches
nothing (status rows, `--dry-run` and previews use it); `other_paths()` lists
every other layout on disk with its number. `open()` creates the base's folder
and holds the layout's in-use lock, `<layout path>.inuse`, until the process
exits: every reader shares it, and only whoever moves a layout takes it
alone. Open the store through `open()` every time, before the file exists.

A store holds only data you can recompute: never settings, state, or anything
a user typed. Keying it by profile, when a setting changes what it holds, is
yours to do inside it. `untaped lint` reports `lint.store-format` where a
plugin passes a `plugin_dir(...)` or `~/.untaped` path straight to
`sqlite3.connect`, `shelve.open` or `dbm.open`; a file whose layout every
release of the major reads waives it with `# untaped: allow store-format`.
Composition quarantines a plugin that lists another plugin's store
(`foreign-store`) or the same name twice (`duplicate-store`), and
`untaped doctor`'s `stores` row shows each store's layout and file, and
every other layout on disk with what holds it (below).

A cache that describes a store follows it, `Cache(..., store=CATALOGUE)`: its
sidecars live in `caches/<name>.v<layout>/`, so a key is never fresh over
another layout's file. An answer cache (`@cached`) follows no store.

A store that predates 11.1 declares what its bare file holds: `bare=4` says a
file at `base` itself, with no layout in its name, was written at layout 4.
The first `open()` at that layout renames it to the layout's name, then the
folders of the caches that follow the store: same bytes, no rebuild. While
another process has it open, or a SQLite `-journal`, `-wal` or `-shm` sits
beside it, `open()` returns the bare path and a later open renames it. A
store born in 11.1 or later leaves `bare` unset.

A SQLite store whose schema grows inside one layout pins its history with
`untaped.testing.assert_schema_pinned(ddl, version=, additive_since=,
pins=)`: a folder of `v<N>.sql` files, one per released schema, and a
`pins.json` of their hashes. It fails when the DDL changed without a new
version, when a released file was edited, and when a version claimed as
additive changed an existing table or added a column an older writer cannot
insert without (not null, no default). Review watches `pins.json`: a
released pin is appended once and never edited, since editing a file and its
pin together passes the check.

### Who holds a layout, and cleanup

Each install of untaped that runs `untaped setup register` writes a
*reference* in `~/.untaped/refs/`: where it is installed, its version, and
the layouts its plugins' stores use. A target is written
`<plugin>.<store>@<layout>` for a layout (`acme.catalogue@2`) or
`<plugin>.<store>#<item>` for one item of a store that keeps one per item;
the install is the only holder in 11.1, and it holds layouts. An install refreshes its own reference whenever a
command opens a store after its plugins or their layouts changed, so an
upgrade needs nothing more. `holders(target)` (experimental) names the
references that keep a target, and raises while a reference cannot be read
(who holds it is unknown then, never nobody). A reference is *live* while its folder still
holds an untaped install; one written by a newer untaped, or one this untaped
cannot read, is kept as if live and pauses all cleanup until this install is
upgraded.

Cleanup is automatic. After a fill completes (a cache that follows the store
refreshed with no error, nothing partial), a registered install removes every
other layout of that store that no live reference holds and no running
untaped has open: it takes the layout's in-use lock alone, without waiting,
moves the layout aside with its SQLite sidecars, its caches' folders and its
lock, then deletes it. Each removal is an info line of kind `untaped.pruned`
(`store`, `layout`, `path`, `bytes`) that `--quiet` does not hide. An
unregistered install, or one run from a source tree, never removes anything,
and neither does any install while `refs/` cannot be read.

A layout a setting may point at, such as an index path a profile sets to an
older layout's file, is that profile's data. `Store(..., keep=…)` says so: a
function of a layout's path returning why it stays, or `None`;
`setting_guard("acme", "catalogue_path")` keeps every path any profile or the
environment sets that key to. The cleanup never removes what `keep` names, and
a `keep` that raises keeps the path too.

What the cleanup will not remove on its own (no registered install keeps the
store, a running untaped has it open, a damaged in-use lock, a reference it
cannot read) the `stores` row names with its reason. For the layouts nobody
holds, a store's plugin adds `other_layouts_migration(STORE)` (experimental)
to its `migrations`: `untaped setup migrate-dirs` then offers each one as a
`<plugin>.<store>-layouts` delete row with that reason. It keeps a symlink,
anything `keep` names, and every layout while `refs/` or a reference cannot
be read, since who holds it is unknown then. The references of installs that are gone are core's `untaped.references` row, and
`untaped doctor`'s `references` row lists every reference. In tests,
`untaped.testing.write_reference` writes another install's reference.

## Status rows and tests

For a command that reports a cache's state, subclass `FreshnessRecord` and
fill it with `FreshnessRecord.fields(MY_CACHE.freshness(key), max_age=…,
now=…)`; its `refresh_error` never fails the row, and `outdated` says the
key was filled by another version. `cached(key, read)` peeks
at what is cached with no refresh, lock or warning, for in-process readers
such as a screen; `seed()` and `forget()` record what an owner already holds
or forget a key whose data changed. In tests, `untaped.testing.cache_clock()`
moves the clock a cache reads. The example plugin (`examples/untaped-hello`,
`hello motd`) has one cache.
