Default table columns follow the order json shows a record's fields, so some
tables list them in another order: an outcome's `action` follows the field
naming the row (`project` and `setup prune` outcomes, `project remove`'s
preview), and a check's `status` comes after its own columns. Among them:
`auth status`, `doctor`, `setup plan`, `setup register`, `plugin check`,
`ansible source status`, `ansible deps` and `impact`, `awx jobs events`,
`awx` `launch` and `sync`, `awx apply`, `github repos list` and `search code`,
and `lint` and `awx lint`, where each finding's `location` now comes last.
`plugin new` and `lint new` show every field of their rows. The json, yaml,
pipe and raw output is unchanged.
