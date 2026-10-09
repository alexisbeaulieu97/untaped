`doctor` and `setup` JSON, YAML and pipe rows carry the fix in a `fix` field
(an argv list) and an `automatic` flag (true when the fix needs no value or
input) instead of appending the fix command to `detail`. A plugin's doctor
check marks such a fix with `DoctorResult(automatic=True)`, and plugin code
that reads `DoctorResult.fix` must also handle an argv list.
([#457](https://github.com/alexisbeaulieu97/untaped/pull/457),
[#478](https://github.com/alexisbeaulieu97/untaped/pull/478))
