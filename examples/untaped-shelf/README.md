# untaped-shelf

An example contract owner for [untaped](https://github.com/alexisbeaulieu97/untaped):
it declares the `BookSource` contract in `untaped_shelf.api` and asks every
plugin that fills it (`untaped shelf list`, `untaped shelf find TITLE`). Its
provider counterpart is [`untaped-library`](../untaped-library). Neither is
published.

Install both into one virtualenv, beside an `untaped` wheel, the way
[`untaped-hello`](../untaped-hello/README.md) describes, then run each
package's `tests`. The rules a contract follows are in
[`docs/contracts.md`](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/contracts.md).
