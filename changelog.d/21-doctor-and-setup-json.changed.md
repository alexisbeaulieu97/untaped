`doctor` and `setup` JSON, YAML and pipe rows carry their fix as a `fix`
argv and `automatic` (true when the fix needs no value or input) instead of
appending the fix command to `detail`; read `fix` instead. A plugin's doctor
check marks such a fix with `DoctorResult(automatic=True)`, and plugin code
that reads `DoctorResult.fix` must also handle an argv list.
([#457](https://github.com/alexisbeaulieu97/untaped/pull/457),
[#478](https://github.com/alexisbeaulieu97/untaped/pull/478))
