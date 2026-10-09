The workspace picker (`workspace create`, `workspace add`) is full screen and
follows the theme's colors, symbols and border style (`ui.symbols` gains
`heading` and `dash`). Selections show as `[✓]` like other multi-choice lists,
its footer names tab, enter and ctrl-s (create), a redirected stderr no longer
stops it (it draws on the controlling terminal; `ui.pick_many` does the same
for piped stdin), and esc outside the search asks before discarding a
selection. SDK: `Screen(shared_labels=...)` says what a shared key does on
that screen, for its footer and help overlay, and a screen's `update` receives
`Help` when `?` opens the overlay.
([#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
