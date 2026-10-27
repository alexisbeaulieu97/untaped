A plugin's doctor body that builds `DoctorResult` positionally passes
`ok` first: `DoctorResult(True, "detail")`, or drops `id=` and names the
rest. One passing `id=` keeps working.
