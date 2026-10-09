# Contracts

A contract is an interface one plugin (its owner) declares and others fill,
so the owner never names who answers: workspace asks for repositories without
knowing whether GitHub, GitLab or both supply them. Everything lives in
`untaped.contracts`, whose docstrings are the reference; `untaped.sdk` never
imports it, so a command that asks no contract pays nothing.

```python
# the owner, in its api module
class Book(Issued, kind="shelf.book"):
    title: str


@experimental
class BookSource[T: Record = Book](Contract):
    @bridge
    def to_book(self, item: T) -> Book:  # filled only when T is not Book
        raise NotImplementedError

    @cached(ttl=timedelta(hours=1))
    @abstractmethod
    def books(self) -> list[Book]: ...


SPEC = PluginSpec(name="shelf", contracts=lambda: (BookSource,))

# the owner, asking
book = select_one(gather(BookSource.books)(), lambda b: b.title == "Dune")

# a provider
class Library(BookSource[Volume], Configured[LibrarySettings]):
    def to_book(self, item: Volume) -> Book:
        return Book(title=item.name)

    def books(self) -> list[Book]:
        return [self.to_book(Volume.model_validate(row)) for row in self.http.get_json_list(...)]


SPEC = PluginSpec(name="library", settings=LibrarySettings, provides={"shelf": _shelf})
```

- **Declaring.** A contract subclasses `Contract` directly, with required
  methods `@abstractmethod` and optional ones raising `NotImplementedError`,
  and is marked `@experimental`, so it may change in its owner's minor
  release (a renamed method keeps its old name marked
  `@deprecated(replacement=...)`). Its name is the class's in snake_case
  (`book_source`). Method names may not shadow a builtin or the provider
  machinery (`providers`, `gather`, `convert`, `on`, `own`, `ready`,
  `settings`, `http`), and every parameter and return type must have a JSON
  schema, with no `*args`, positional-only parameters or non-JSON defaults:
  the class definition raises `TypeError` (`unserialisable-signature`)
  otherwise. `shell=` is reserved for shell plugins. `@cached` is refused on
  a method that returns a secret.
- **The owner's model** is the item type parameter's default, an `Issued`
  record with a kind. A provider issuing its own record type passes it
  (`BookSource[Volume]`) directly, never through a generic base of its own,
  and fills every `@bridge` method; the bridge stamps `source` with the
  provider's plugin and record. A provider of the owner's model fills no
  bridge.
- **Filling.** `PluginSpec.provides` maps the owner's name to a function
  returning provider instances, with a local import (a provider class that
  breaks a rule raises when it is defined, so only a local import keeps
  that to its own offer). The contract decides what is `@cached`; a
  provider never adds it. `Configured[S]` gives
  `self.settings` (S is the plugin's `settings` model), and a provider is
  ready when they validate; override `ready()` only for what a schema can't
  say. Use `self.http` for requests: it carries the profile's proxy, CA and
  timeout, and `gather(deadline=...)` bounds it (as it does any untaped
  `HttpClient`; another library's client is not bounded). Keep helper methods private
  (`_name`): doctor reports a public method the contract doesn't have as
  `unused-method`.
- **Asking.** `gather(method, refresh=, needs=, deadline=)(*args)` returns
  one `Ok`, `Failed` or `Skipped` per provider, in rank order, each item
  validated once as the owner's model. `select_one(answers, matches)` picks
  one item and raises the deciding provider's error, `NotFound` or
  `Ambiguous`; `convert(Contract.bridge_method, envelope)` reads a piped
  record. [How providers are loaded and chosen](#how-providers-are-loaded-and-chosen)
  gives the rules they follow.

## How providers are loaded and chosen

A contract's providers are
loaded on the first ask for one of its owner's contracts, once per process
and profile.
A provider that breaks a rule is quarantined alone, never with the plugin's
commands or its other offers: `missing-bridge` (it issues its own records but
doesn't fill the bridge), `unresolved-item-type`, `duplicate-kind`,
`unserialisable-signature`, `owner-not-installed`, or `bad-provider` (its
`provides` function raised or returned something else). `untaped doctor`'s
`contract-providers` row names each one, and each provider method the
contract no longer has (`unused-method`), which is simply never called.

Each ask goes to every provider that fills the method. One not configured in
the profile is skipped silently; with none ready the command exits 4.
`select_one` then decides:

- A ranked provider is above every unranked one; unranked ones are never
  above each other.
- With any provider failed, a match from one ranked above every failure
  wins, with a warning; otherwise the first failure's own error and exit
  code stand. With nothing ranked, any failure blocks.
- A stale cached listing confirms a match, never an absence.
- A quarantined provider, or one that returned invalid items, is skipped
  with a warning when unranked and fails when ranked.
- Otherwise no match exits 2 (not found), and several matches with nothing
  to rank them exit 2 (ambiguous).
