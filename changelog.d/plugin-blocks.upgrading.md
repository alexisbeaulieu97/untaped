Pass a `DoctorCheck`'s `title` and `run` by keyword (`api =
DoctorCheck(title="acme API", run=probe)` on the plugin class), and build a
changed copy of one, or of a `DeprecatedBehaviour`, with a new call instead
of `dataclasses.replace`.
