# Contracts

A contract is an interface one plugin (its owner) declares and others fill,
so the owner never names who answers: workspace asks for repositories without
knowing whether GitHub, GitLab or both supply them. Everything lives in
`untaped.sdk.contracts`, whose docstrings are the reference; `untaped.sdk` never
imports it, so a command that asks no contract pays nothing.

```python
# the owner, in its api module
class Book(Issued):  # kind shelf.book: its class name
    title: str


@experimental
class BookSource[T: Record = Book](Contract):
    def to_book(self, item: T) -> Book:  # filled only when T is not Book
        raise NotImplementedError

    @cached(max_age=timedelta(hours=1), label="books")
    @abstractmethod
    def books(self) -> list[Book]: ...


class Shelf(Plugin):
    """Find books."""

    settings = ShelfSettings
    commands = (List, Find)
    offers = (BookSource,)

# the owner, asking
book = select_one(gather(BookSource.books)(), lambda b: b.title == "Dune")

# a provider
class LibraryBooks(BookSource[Volume], Provider[LibrarySettings]):
    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name)

    def books(self) -> list[Book]:
        with self.ctx.http.client(self.settings, base_url="https://library.example") as client:
            return [self.to_book(Volume.model_validate(row)) for row in client.get_json_list(...)]


class Library(Plugin):
    """Lend books to shelf."""

    settings = LibrarySettings
    fills = (LibraryBooks,)
```

- **Declaring.** A contract subclasses `Contract` directly, with required
  methods `@abstractmethod` and optional ones raising `NotImplementedError`,
  and is marked `@experimental`, so it may change in its owner's minor
  release (a renamed method keeps its old name marked
  `@deprecated(replacement=...)`). Its name is the class's in snake_case
  (`book_source`). Method names may not shadow a builtin or an attribute
  the provider bases define (`ready`, `own`, `ctx`, `settings`), and every parameter and return type must have a JSON
  schema, with no `*args`, positional-only parameters or non-JSON defaults:
  the class definition raises `TypeError` (`unserialisable-signature`)
  otherwise. `shell=` is reserved for shell plugins. The types are the
  declaration: a method returning a `list` is a listing, whose rows stand
  alone (an invalid row is dropped and the rest kept, where any other
  method's invalid item fails the provider's whole answer), and the method
  taking `T` and returning the owner's model is the bridge (a contract has
  at most one). `@cached(max_age=, label=)` keeps answers per provider, its
  settings and the arguments in a [`Cache`](caches.md) core makes for the
  owner, named `<contract>_<method>` (its user setting is
  `<owner>.caches.<name>.max_age_seconds`); it is refused on a method that
  returns a secret, and a `Cache` passed to `@cached` caches that one method.
  A contract has one owner, the plugin whose `offers` lists it: two plugins
  listing it are both quarantined (`duplicate-offer`), and so is a plugin
  refreshing a cache of a contract no composed plugin owns (`unowned-cache`).
- **The owner's model** is the item type parameter's default, an `Issued`
  record (`Issued` and its `Source` are also in `untaped.sdk`, so an owner's
  state model can hold issued records without loading contracts). Its kind,
  like a provider's own record's, is its class name under the plugin that
  lists it (the owner's `offers`, the provider's `fills`), given at
  composition. A provider issuing its own record type passes it
  (`BookSource[Volume]`) directly, never through a generic base of its own,
  and fills the bridge, which stamps `source` with the
  provider's plugin and record. A provider of the owner's model fills no
  bridge.
- **Filling.** A provider subclasses the contract and `Provider[S]`, as a
  command is `Command[S]`: `self.settings` is the plugin's section (S is the
  plugin's `settings` model, which composition checks), plain `Provider`
  when the plugin reads none, and `self.ctx` has a command's `diag`,
  `http`, `project`, `plugin` and `plugins` but no terminal, since only the command
  asks. The plugin class lists its providers in `fills`; core reads each
  one's owner from the contract it subclasses and builds it at the first
  ask that reaches it, never at composition. A provider is ready when its
  settings validate; override `ready()` only for what a schema can't
  say: return `NotReady(reason, key(S).field)`, the key being the setting
  that would make it ready, which core spells in full for doctor and the
  `config set` hint (`NotReady.spelled`). The contract decides what is
  `@cached`; a provider never adds it. Build HTTP clients with
  `self.ctx.http.client(self.settings, ...)`, as a command does: they carry
  the profile's proxy, CA and timeout. Keep helper methods private
  (`_name`): doctor reports a public method the contract doesn't have as
  `unused-method`, unless its function carries
  `__untaped_provider_helper__ = True` (a provider base's own decorator sets
  it). A contract method never changes process-global
  state: `gather` runs providers side by side in threads, so a change would
  reach the providers beside it. Composing the root is the guarded case:
  `compose_root`, `build_root_app` or `compose_with` inside a provider call
  raises, and the provider's answer is `Errored`.
- **Asking.** `gather(method, plugins=, refresh=, progress=)(*args)` returns
  one `Ok`, `Errored` or `Skipped` per provider that fills the method (only
  those `plugins` when given: provider classes where the code holds one,
  plugin names at the user edge), in rank order, each item validated once
  as the owner's model. The views `.ok`, `.errored` and `.skipped` split
  them by outcome, so no caller writes `isinstance` loops. A skip's or
  error's `reason` is a `Reason` (`Reason.not_configured`,
  `Reason.raised`...), each answer typed by the reasons it can carry; its
  value is the word on the wire (`not-configured`). `progress` counts the providers on the caller's task as they
  answer; a provider never gets the handle.
  `select_one(answers, matches)` picks one item and raises the deciding
  provider's error, `NotFound` or `Ambiguous`.
- **Reading piped rows.** `stdin: Stdin[Book] = Stdin(via=BookSource.to_book)`
  takes `shelf.book` rows and the rows of any provider's own record
  (`library.volume`), each read through that provider's bridge, so
  `self.stdin.values(given)` returns each argument as given, a bare
  identifier or a `Book`, in input order. The declaration records the
  contract, so the surface lists the providers' kinds the command reads under
  the composed install. A provider is built, and its
  settings read, only when a row of its kind arrives. A kind no provider
  reads, or a line with no kind, exits 2; an unready provider whose row
  arrived, or a bridge returning an invalid `Book`, exits 4. `convert`, the
  11.x reader, leaves in 12.0.
  [How providers are loaded and chosen](#how-providers-are-loaded-and-chosen)
  gives the rules they follow.

## How providers are loaded and chosen

A contract's providers are
loaded on the first ask for one of its owner's contracts, once per process
and profile.
A provider that breaks a rule is quarantined alone, never with the plugin's
commands or its other offers: `missing-bridge` (it issues its own records but
doesn't fill the bridge), `unresolved-item-type` (its record has no kind), `duplicate-kind`,
`unserialisable-signature`, `owner-not-installed`, `owner-out-of-range` (the
plugin's extra named like the owner requires a range of it the installed
owner is outside), or `bad-provider` (building it raised). A class in
`fills` that is no `Provider`, or reads another plugin's settings, is a
composition problem (`bad-provider`) that quarantines its plugin. A provider method
the contract no longer has (`unused-method`) is simply never called.

Each ask goes to every provider that fills the method. One not configured in
the profile is skipped silently; with none ready `gather` raises
`NoProviderReady` (exit 4), whose `not_ready` maps each plugin to why it
isn't ready and `quarantined` each refused offer to its reason (both empty:
no composed plugin fills it; a plugin quarantined at composition offers
nothing and is only in `composition().quarantine`), so an owner can turn it
into its own error (workspace reports a repo name nobody could look up as not found, naming the
setting to set). A provider that raises is `Errored` with `reason` `Reason.raised`.
`select_one` then decides:

- A ranked provider is above every unranked one; unranked ones are never
  above each other.
- With any provider failed, a match from one ranked above every failure
  wins, with a warning; otherwise the first failure's own error and exit
  code stand. With nothing ranked, any failure blocks.
- A stale cached answer confirms a match, never an absence. Its
  `untaped.stale` warning, naming the failed refresh and the answer's age,
  prints when the answer is used: `select_one` prints it for the match it
  returns, and any other reading of the answers prints it the first time;
  a stale absence fails with the provider's error alone, or, when a match
  ranked above it wins, warns with that error and keeps the line for the
  next reading of the answers. A consumer that reports staleness itself
  takes the lines first with `Answers.take_stale()` and prints, drops or
  raises them.
- A quarantined provider, or one that returned invalid items, is skipped
  with a warning when unranked and fails when ranked.
- Otherwise no match exits 2 (not found), and several matches with nothing
  to rank them exit 2 (ambiguous).

## Ranking providers

A contract owner's config section holds an `extensions` key the SDK adds,
with a ranking per contract method. No plugin's settings or state model may
declare `extensions`, nor `caches`, kept for cache settings; one that
does is quarantined (`bad-settings-keys`).

```yaml
workspace:
  extensions:
    repo_source:
      rank:
        repos: [github, gitlab]
```

`untaped plugin rank workspace.repo_source repos github gitlab` writes that
ranking in the profile; naming no plugin removes it. The command refuses a
contract or method the owner doesn't have, and a bridge method, which is
never ranked. The environment overrides it like any setting:
`UNTAPED_WORKSPACE__EXTENSIONS__REPO_SOURCE__RANK__REPOS='["github","gitlab"]'`.
Loading config checks only the names' spelling, so a ranking in
the base never breaks a profile where a ranked plugin isn't
installed or configured.

`untaped plugin contracts` lists every contract method with its
owner, stability, the providers that fill it in the order they are asked
and the profile's ranking, including contracts nobody fills yet.
`untaped plugin schema <kind>` prints a record or diagnostic kind's JSON
Schema. A field's description there is its `Field(description=...)`, written
for whoever reads the JSON (`null`, Markdown): pydantic leaves an attribute
docstring out.

`untaped doctor` has a `contract-provider` row per provider, titled with
one of these reasons:

- passing: `active`, `not-configured` (it names the setting it waits for)
  and `owner-not-installed`;
- warning: `unused-method`, `owner-schema-drift` (the provider was tested
  against another schema of the contract than the installed owner's; see
  [Testing contracts](#testing-contracts)) and the quarantine reasons above.

A `contracts` row warns about an owner whose `contracts` function fails
(`bad-contracts`), and a `rank` row warns about a ranking it can't follow:
`rank-unknown-method` (no such contract or method) and `rank-not-installed`
(a ranked plugin isn't installed), each with the `plugin rank` command that
repairs it in the profile holding the ranking (none when an environment
variable sets it).

## Testing contracts

`untaped.sdk.testing` has the tools, and loads `untaped.sdk.contracts` only when one
runs.

An owner tests its questions against providers it controls, and keeps its
contracts from changing by accident:

```python
class FakeBooks(BookSource, Provider):
    def books(self) -> list[Book]:
        return [Book(title="Dune")]


def test_shelf_lists_a_provider_s_books(install) -> None:
    ran = install(Shelf, providers={"fake": (FakeBooks,)}).run(List())
    assert [book.title for book in ran.rows_as(Book)] == ["Dune"]


def test_shelf_keeps_its_surface() -> None:
    assert_surface(Shelf)
```

- `install(Shelf, providers={"fake": (FakeBooks,)})` composes the owner
  plus a plugin named `fake` filling the contracts its classes subclass;
  `install(Library)` composes the owners a provider declares under their
  extras too. Outside tests, `untaped.sdk.contracts.providing(owner,
  {"house": [...]})` does the same for an owner that loads providers from
  elsewhere (a repository's rule modules): the installed plugins plus a
  plugin `house` offering each instance to `owner`. A key that is not a
  plugin name, is reserved (`untaped`, `sdk`, a core section or command
  group) or is an installed plugin's name is refused before anything is
  composed, and one the composition quarantines raises `ConfigError`.
- `assert_surface(Shelf)` (experimental, with the file's line format)
  writes `snapshots/surface.txt` beside the test the first time: one line per promise of the plugin,
  its commands, flags and settings, each contract with its methods'
  parameter and return types, and every model they reach with its fields.
  It fails on an incompatible change, listing the lines that went: a
  removed or retyped field, a new required field, flag or parameter, a
  changed constraint, a dropped Literal value, a changed default, a
  removed stable command or method, a new abstract method, or any change
  to a contract method's signature, since every provider must match it
  exactly. A new optional
  method, optional payload field or Literal value, a new command or
  optional flag, and anything removed while experimental are compatible
  and rewrite the file. To accept a breaking change in a major release,
  delete the file. Commit it. With `version=` (your distribution's
  version) it also fails an undated deprecation and anything still marked
  removed in that version's major or earlier
  ([Deprecating something](./reference/deprecation.md#the-checks)). A stable contract method that reaches a model
  marked `@experimental` fails here, so a contract is never stabilised with
  a model nobody meant to promise.
- An owner may ship checks every provider must pass as a
  `conformance(provider)` function in its `testing` module
  (`untaped_shelf.testing`); `untaped lint` runs it on each ready
  provider.

A provider proves it fills its contract:

```python
def test_the_library_fills_the_shelf_contract() -> None:
    assert_fills(LibraryBooks, samples=[Volume(shelf_mark="A1", name="Dune")])
```

- `assert_fills(Provider, samples=[...])` checks that each sample (a `T`
  or a mapping of one) survives a JSON round trip, and that each bridge
  method turns it into a valid owner record whose `source` gives it back.
  `samples=` is required when `T` is not the owner's model. Run from a
  checkout or an editable install, it records the owner's schema hash in
  the plugin package's `fills.json`; commit it.
- `untaped lint`, in a plugin's directory, checks the installed plugin, a
  developer's editable install included ([Lint](./lint.md)): the
  conventions and the import lint, then, composing only the plugin and the
  owners it provides for, the owner's `conformance`, one live call of each
  filled method that takes no required argument (each item round-tripped),
  the `assert_fills` checks on those items, and the recorded schema hash
  against the installed owner's. A provider that isn't ready skips the live
  checks. A different hash is `owner-schema-drift`, which warns (here and in
  `doctor`) and never fails: rerun the provider's tests against that owner.
  It exits 3 when a check fails. `untaped.sdk.testing.check_plugin(NAME)`
  returns the rows of these checks, without the conventions that read only
  the plugin's files, for a linter to judge; `check(install(...))` fails a
  test on any of them.
- [Filling another plugin's contract](./plugins.md#filling-another-plugins-contract)
  covers starting a provider with `untaped plugin new`.
