`doctor` and `setup` JSON, YAML and pipe rows no longer append the fix
command to `detail`; read `fix` instead, and plugin code that reads
`DoctorResult.fix` must also handle an argv list.
([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
