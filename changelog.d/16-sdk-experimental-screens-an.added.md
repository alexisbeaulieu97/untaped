SDK (experimental): screens, an Elm-style runtime for full-screen terminal UIs
(`Screen`, `Cmd`, `UiContext.run`, `untaped.testing.drive_screen`), with
input components (`TextInput`, `SecretInput`, `Select`, `Tabs`, …) and
`SearchList`, `Viewport`, `Tree`, `Tags`, `Form` and `Panes`. A screen's
`shared_labels` says what a shared key does there, for its footer and help
overlay, and its `update` receives `Help` when `?` opens the overlay. See
[Screens](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/screens.md).
([#525](https://github.com/alexisbeaulieu97/untaped/pull/525),
[#526](https://github.com/alexisbeaulieu97/untaped/pull/526),
[#527](https://github.com/alexisbeaulieu97/untaped/pull/527),
[#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
