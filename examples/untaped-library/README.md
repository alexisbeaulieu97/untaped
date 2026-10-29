# untaped-library

An example contract provider for [untaped](https://github.com/alexisbeaulieu97/untaped),
in the 12.0 shape: the plugin is a class, `Library` in `plugin.py`, whose
`fills` lists `LibraryBooks`, a `Provider[LibrarySettings]` that fills
[`untaped-shelf`](../untaped-shelf)'s `BookSource` contract with the volumes
listed under `library.volumes`, issuing its own `Volume` records through the
contract's bridge. It has no commands of its own. It requires `untaped-shelf`
in its dependencies, as every provider requires the owner whose contract it
subclasses. It is not published.

Its tests run the pair together: install `untaped`, `untaped-shelf` and this
package into one virtualenv (see [`untaped-hello`](../untaped-hello/README.md)),
then run `tests`. `install(Library, settings=LibrarySettings(...))` composes
shelf too, the owner library declares, so `.cli("shelf list")` asks the
library's provider.
