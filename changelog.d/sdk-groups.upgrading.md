A plugin imports `untaped.sdk.contracts` instead of `untaped.contracts` and
`untaped.sdk.testing` instead of `untaped.testing`, and its
`tests/conftest.py` names `untaped.sdk.testing.plugin`. A name that moved to
a group is imported from it (`from untaped.sdk.storage import Cache`,
`from untaped.sdk.network import HttpClient`); an import of it from
`untaped.sdk` fails with `ImportError`. A screen imports from
`untaped.screen.core` and `untaped.screen.components`.
The names that were core's own (`read_stdin`, `table_row`, `HttpSettings`,
`connected_client`, `Written`, `HolderState`) have no SDK spelling: a
plugin reads its input through the `Stdin` and `Lines` fields and reaches a
server through `HttpClient`.
