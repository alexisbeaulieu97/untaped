`untaped workspace create NAME --empty` makes an empty workspace without
the picker, and the picker now creates one when confirmed with no repos
selected. SDK: `PickRequest(allow_empty=True)` lets a picker confirm an
empty selection.
([#524](https://github.com/alexisbeaulieu97/untaped/pull/524))
