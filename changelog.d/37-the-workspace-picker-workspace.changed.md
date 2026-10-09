The workspace picker (`workspace create`, `workspace add`) is full screen and
follows the theme's colors, symbols and border style. Selections show as
`[✓]` like other multi-choice lists, its footer names tab, enter and ctrl-s
(create), and a redirected stderr no longer stops it: it draws on the
controlling terminal (`ui.pick_many` does the same for piped stdin).
([#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
