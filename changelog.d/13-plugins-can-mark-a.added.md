Plugins can mark a settings field `experimental` or
`deprecated(replacement=…)` too (`Annotated[int, experimental]`,
`Annotated[bool, deprecated(replacement=…)]`), and a capability's settings
inherit its mark. `config list` and `config get` report each setting's
`stability`.
([#519](https://github.com/alexisbeaulieu97/untaped/pull/519))
