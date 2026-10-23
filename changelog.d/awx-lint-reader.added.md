A repository of AWX apply documents lints them with `untaped lint`, offline
(experimental, as `awx lint` is): name awx under `readers:` in
`.untaped/lint.yml` (`awx: ["specs/**/*.yml"]`) and the files those globs
match are checked by the awx rules, and by any rule reading `awx.resources`,
as `untaped awx lint FILES --offline` would.
