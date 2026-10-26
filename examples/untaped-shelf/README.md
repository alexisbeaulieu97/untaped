# untaped-shelf

An example contract owner for [untaped](https://github.com/alexisbeaulieu97/untaped),
in the 12.0 shape: the plugin is a class, `Shelf` in `plugin.py`, which
`offers` the `BookSource` contract declared in `untaped_shelf.api` and asks
every plugin that fills it (`untaped shelf list`, `untaped shelf find
TITLE`). The answers are kept in the cache the class holds as
`book_source_books`, so `shelf.caches.book_source_books.max_age_seconds`
tunes it. `untaped_shelf.testing.conformance` is the check every provider
must pass. Its provider counterpart is [`untaped-library`](../untaped-library).
Neither is published, and like [`untaped-hello`](../untaped-hello/README.md)
they run only against a core built from this repository until the next major.

Install both into one virtualenv, beside an `untaped` wheel, the way
`untaped-hello` describes, then run each package's `tests`. The tests mirror
the package and compose fake providers the test defines:
`install(Shelf, providers={"fake": (FakeBooks,)}).run(List())`. The rules a
contract follows are in
[`docs/contracts.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/contracts.md).
