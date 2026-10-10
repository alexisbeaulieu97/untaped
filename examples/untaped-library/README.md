# untaped-library

An example contract provider for [untaped](https://github.com/alexisbeaulieu97/untaped):
it fills [`untaped-shelf`](../untaped-shelf)'s `BookSource` contract with the
volumes listed under `library.volumes`, issuing its own `library.volume`
records through the contract's bridge. It has no commands of its own. It is
not published.

Its tests run the pair together: install `untaped`, `untaped-shelf` and this
package into one virtualenv (see [`untaped-hello`](../untaped-hello/README.md)),
then run `tests`.
