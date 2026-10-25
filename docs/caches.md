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
gives it the setting `acme.caches.motd.max_age_seconds` (a version-only
cache, below, has none), defaulting to the
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
`plugin_dir(SPEC)/cached/`, in a folder named by a digest of your plugin's
own settings: the values some layer set (never a default, never the cache max
ages or extension ranks untaped adds to your section, never core's `http`)
and where each secret comes from (a variable's name, the config file or a
token command), never its value. Profiles whose section reads the same share
the folder; a changed value or another credential source gets its own. After
a complete fill, the key's files in the folders of settings you no longer use
go once they are older than 30 days and ten times the declared max age, and a
folder left empty goes too. Keep your own store apart the same way (by key,
and by settings when a setting can change what a key holds), or let the cache
keep the data itself (below).
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

## A cache that keeps its data

When what a fill fetches is one value per key, let the cache keep it: declare
the value's pydantic model with `model=`, return it as `Filled(value=...)`,
and `serve()` answers a mapping of each key to its value, with no `read=` and
no store of your own:

```python
class Snapshot(BaseModel):
    items: list[str]

SNAPSHOTS = Cache("acme", "snapshots", label="controller data", max_age=timedelta(hours=1),
                  model=Snapshot)                         # a Cache[Snapshot]

def fill(key: str, handle: ProgressHandle) -> Filled[Snapshot]:
    return Filled(value=Snapshot(items=fetch(key)), summary="12 items")

served = SNAPSHOTS.serve({url: host}, refresh=refresh, fill=fill, partition=token)
snapshot = served.value[url]                               # a Snapshot
```

`model=` also takes a function of no arguments that returns the class, for a
model whose import would load more than a settings module should: it is
called once, on first use. Each key's freshness and value are one file, one
file per version of the value: its model's shape (field names and types,
not docstrings, descriptions or field order) and the cache's `version=`. A
key filled by another version (another install sharing `~/.untaped`, or an
upgrade) is *outdated*: refreshed when it is due, and served, with one
`untaped.stale` warning, under `--cached` or when the refresh fails. A file
that can't be read, or holds another shape, is missing and refilled. After a
complete fill, the key's files of other versions older than the max age are
deleted. `untaped doctor`'s `caches` row counts each cache's keys holding
only another version's value, as a note.

`partition=` keeps another set of files for the same keys, for data that
depends on a credential: it is hashed into the file names and never shown in
a warning or an error. Another partition's files of a key go once they are
older than 30 days and ten times the declared max age. The same variable or
token command with a new value keeps its folder, so a cache whose data depends
on who asks passes the credential as `partition=`.
`split="none"` keeps one folder for every setting, when the key and the
partition already say whose data it is (by default, `split="settings"`).
`cached(key)` peeks at the kept value, `forget(key)` drops every version of
it, and `seed()` and `read=` are refused, since the cache holds the data
itself. A fill of a typed cache that returns no value, or a value of another
class, raises `TypeError`; so does a value from any other cache's fill. In
tests, `untaped.sdk.testing.keep_value(CACHE, key, value, refreshed_at=,
version=)` keeps a value as a fill would, and `damage(CACHE, key)` makes its
file unreadable. Both are experimental.

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

A 12.0 plugin class names the code instead:
`version=installed_code(acme.domain.parser)` hashes every file of that
module or package (a module or its dotted name; neither is imported to find
it), and `version=installed_code()` versions by the whole install: untaped
and every plugin distribution, by version, `RECORD` and, for an editable
install, the modification times and sizes under its source folder (hidden
and editor files, `node_modules` and virtual environments left out). A
`setup.py develop` install counts by its version alone, and files a plugin
reads from outside its source folder never count. A named top-level package
an import hook installs (a setuptools editable finder) is found once
imported.

Each key's file keeps the version it was filled at. A key filled at
another version is *outdated*: due the way a key past its max age is (auto
refreshes it, its data is kept, and after a failed refresh the retry window
holds), and stale: `--cached` serves it with one `untaped.stale` warning whose
`outdated` is true (`source prod: made by another version of the acme plugin;
using data from …`). `seed()` writes no version, so data seeded from an older
release is refreshed on its first read. Two installs at different versions
that share settings (a release and a development checkout) refill each
other's keys on every read. Without `version=` nothing changes.

A value that depends only on the code that computes it, never on an
upstream, is a version-only cache: `max_age=None` with `model=`, `version=`
and `split="none"`. Time never makes it due: it is filled when this version
has no value and served from then on. It has no
`caches.<name>.max_age_seconds` setting and no `--refresh`/`--cached`
(`Refresh` refuses it). It never serves another version's value, and its
complete fill removes the key's other versions. It writes nothing on stderr: a
home it can't write leaves the value in memory, and the next run fills it
again. A fill that raises propagates. Two installs at different versions that
share a home remove each other's value, so each fills again after the other
ran.

A default is not part of the settings digest, so a release that changes a
default leaves every folder where it is. When that changed default alters
what a cache holds, bump the cache's `version=` in the same release (a
`code_version` that hashes the module declaring the default does it by
itself), or keys filled under the old default are served as if they were
current.

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
a user typed. Keying it by settings, when a setting changes what it holds, is
yours to do inside it. `untaped lint` reports `lint.store-format` where a
plugin passes a `plugin_dir(...)` or `~/.untaped` path straight to
`sqlite3.connect`, `shelve.open` or `dbm.open`; a file whose layout every
release of the major reads waives it with `# untaped: allow store-format`.
Composition quarantines a plugin that lists another plugin's store
(`foreign-store`) or the same name twice (`duplicate-store`), and
`untaped doctor`'s `stores` row shows each store's layout and file, and
every other layout on disk with what holds it (below).

A cache that describes a store follows it, `Cache(..., store=CATALOGUE)`: its
files live in `cached/<name>.v<layout>/`, so a key is never fresh over
another layout's file. An answer cache (`@cached`) follows no store.

A store that predates 11.1 declares what its bare file holds: `bare=4` says a
file at `base` itself, with no layout in its name, was written at layout 4.
The first `open()` at that layout renames it to the layout's name, same
bytes, no rebuild, and removes the folders untaped 11.0 kept for the caches
that follow the store: an 11.0 run would call the file it rebuilds fresh.
Reseed what you can from the data itself (`seed()`). While
another process has it open, or a SQLite `-journal`, `-wal` or `-shm` sits
beside it, `open()` returns the bare path and a later open renames it. A
store born in 11.1 or later leaves `bare` unset.

A SQLite store whose schema grows inside one layout pins its history with
`untaped.sdk.testing.assert_schema_pinned(ddl, version=, additive_since=,
pins=)`: a folder of `v<N>.sql` files, one per released schema, and a
`pins.json` of their hashes. It fails when the DDL changed without a new
version, when a released file was edited, and when a version claimed as
additive changed an existing table or added a column an older writer cannot
insert without (not null, no default). Review watches `pins.json`: a
released pin is appended once and never edited, since editing a file and its
pin together passes the check.

### Who holds a layout, and cleanup

Each install of untaped that runs `untaped setup register` writes a
*reference* in `~/.untaped/plugins/untaped/refs/`: where it is installed, its
version, and the layouts its plugins' stores use. Each target is a `RefTarget`
(experimental), written in the file as an object: a layout, `{"plugin":
"acme", "store": "catalogue", "layout": 2}`, or one item of a store that keeps
one per item, `{"plugin": "git", "store": "store", "item":
"github.com/acme/pay"}`. Who the reference is for is a `RefHolder`
(experimental), written as `{"kind": "install", "name": "3f9a1c2e8b7d"}` in
`refs/install-3f9a1c2e8b7d.json`; the install is the only holder in 11.1, and
it holds layouts. An install refreshes its own reference whenever a command
opens a store after its plugins or their layouts changed, so an upgrade needs
nothing more. `holders(target)` (experimental) returns the `RefHolder` of each
live reference that keeps a target, and raises while a reference cannot be
read (who holds it is unknown then, never nobody). A reference is *live* while
its folder still holds an untaped install; one written by a newer untaped, or
one this untaped cannot read, is kept as if live and pauses all cleanup until
this install is upgraded. A reference of a kind this untaped does not know,
written by a later untaped, is always live and keeps the targets it can read;
in this file format it never pauses cleanup and is never offered for removal.

Cleanup is automatic. After a fill completes (a cache that follows the store
refreshed with no error, nothing partial), a registered install removes every
other layout of that store that no live reference holds and no running
untaped has open: it takes the layout's in-use lock alone, without waiting,
moves the layout aside with its SQLite sidecars, its caches' folders and its
lock, then deletes it. Each removal is an info line of kind `untaped.pruned`
(`plugin`, `store`, `layout`, `path`, `bytes`) that `--quiet` does not hide. An
unregistered install, or one run from a source tree, never removes anything,
and neither does any install while `refs/` cannot be read.

The same goes for the `caches/` folder untaped 11.0 kept in each plugin's
folder, which no later release reads: after any of the plugin's caches
completes a fill, a registered install removes it, with one `untaped.pruned`
line whose `store` and `layout` are `null`.

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
be read, since who holds it is unknown then. The references of installs that
are gone are core's `untaped.references` row, and `untaped doctor`'s
`references` row lists every reference. A plugin uninstalled here leaves its
`cached/` folder, since another install sharing the home may still use it:
`doctor`'s `caches` row notes it, and core's `untaped.uninstalled-caches`
row offers to delete it. In tests,
`untaped.sdk.testing.write_reference` writes another install's reference.

## Other files your plugin keeps

Any other file your plugin keeps for itself, whose place you decide (a
file in a repo, a backup's description), goes on core's data file rather
than a JSON format of your own (all experimental):
`write_data_file(path, value, refreshed_at=None, exclusive=False)` writes a
pydantic model's fields at the top level beside a stamp naming the format and
the untaped version that wrote it (`{"untaped": {"file": 1, "by": "11.1.0"},
...}`), atomically and owner-only; `exclusive=True` creates it whole only if
it is absent and returns whether it did. `read_data_file(path, Model)`
answers a `DataRead`: `Found(value, refreshed_at, by)` (`by` is the
untaped version that wrote it), `Absent()`,
`Unreadable(reason)` or `OtherVersion(file, by)` when a newer untaped wrote
it, so a damaged file is never mistaken for a missing one. A file an earlier
release of yours wrote without the stamp reads with `unstamped="accept"`, in
the model's shape. `copy_data_file(source, target)` copies one with its
freshness cleared. A model may not name a field `untaped`: that key is the
stamp's.

A 12.0 plugin names the file and its model once: `DataFile[Mark].at(path)`
reads with `read()` (no unstamped files) and writes with `write(value,
refreshed_at=, exclusive=)` under the file's lock (a 10 s wait; the lock
lives under the home's `locks/`, never beside the file). A write keeps the
keys a newer untaped added and never writes over a newer untaped's file or
one it cannot read; `copy_to(path)` copies a file that reads, the same way.
An operation that must not run twice at once declares a lock on the plugin
class, `sync = Lock(wait=timedelta(minutes=10))`: `with
DotfilesPlugin.sync.locked():` waits that long for another process
(`dotfiles.sync is busy (another untaped process)` after it),
`try_locked()` yields whether it got it, and taking it again in one thread
only counts.

A folder of the plugin's own is a `Dir` on the plugin class. `notes =
Dir()` is `plugins/<plugin>/notes/` under the home; `checkouts =
Dir(at=key(WorkspaceSettings).workspaces_dir)` is the folder a path
setting names, which must be absolute. `file="log.txt"` makes it one file
in that folder. `layout=N` numbers what it holds as a store numbers its
layouts, inside the folder (`images/images.v4/`, or `index.v4.sqlite3` with
`file="index.sqlite3"`), so two installs never open each other's data and
nothing numbered lands beside the user's folder. `path` says where it is,
`open()` creates the folder and returns the path (a numbered one holds its
layout for the process). `locked(name)` holds the lock of one thing in the
place for the declared `wait=` and `try_locked(name)` tries once;
`locked()` is the place's own lock, separate from its things' locks, so a
plugin uses one or the other. The lock files live under the home
(`plugins/<plugin>/`), never in the place. Locks are taken in one order: a
plugin's own before that of a plugin it requires, so workspace's lock
inside git's fails, naming both.
A `Dir` cannot be named `cached`, `state` or `untaped`: core keeps those.

## Status rows and tests

For a command that reports a cache's state, subclass `FreshnessRecord` and
fill it with `FreshnessRecord.fields(MY_CACHE.freshness(key), max_age=…,
now=…)`; its `refresh_error` never fails the row, and `outdated` says the
key was filled by another version. `cached(key, read)` (`cached(key)` for a
cache with `model=`) peeks at what is cached with no refresh, lock or warning, for in-process readers
such as a screen; `seed()` and `forget()` record what an owner already holds
or forget a key whose data changed. In tests, `untaped.sdk.testing.manual_clock()`
moves the clock a cache reads (`clock.advance(hours=1)`). The example plugin (`examples/untaped-hello`,
`hello motd`) has one cache.
