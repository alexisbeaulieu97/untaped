Default table columns follow the order json shows a record's fields, so some
tables list them in another order. An outcome's `action` follows the field
naming the row (`project` outcomes, `project remove`'s preview), or comes
first where that field is not shown (`project move` and `release`). A
check's `status` comes after its own columns. Among them: `auth status`,
`doctor`, `setup plan`, `setup register`, `plugin check`,
`ansible source status`, `ansible deps` and `impact`, `awx jobs events`,
`awx` `launch` and `sync`, `awx apply`, `github repos list` and `search code`,
and `lint` and `awx lint`, where each finding's `location` now comes last.
`plugin new` and `lint new` show every field of their rows. The json, yaml,
pipe and raw output is unchanged.
