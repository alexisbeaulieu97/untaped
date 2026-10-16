A plugin's Python API can deprecate a function or one of its parameters with a
date: `@deprecated(removed_in=3, replacement=new_api)` over the
`warnings.deprecated` literal type checkers read, and
`@deprecated_parameters` on a function whose parameter carries
`Annotated[..., deprecated(removed_in=3)]`, which then raises an
`UntapedDeprecationWarning` whenever that parameter is passed. `untaped lint`
checks that the two marks agree (`lint.deprecation-text`, with the literal to
paste), that no deprecation is undated (`lint.undated-deprecation`) and that
neither half is forgotten (`lint.untyped-deprecation`,
`lint.unwarned-parameter`, `lint.mark-on-overload`)
([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
