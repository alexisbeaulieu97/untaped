`DoctorResult.fix` takes a command named by its class (`Refresh.spelled()`),
so a doctor check's fix follows the command when it is renamed or mounted
elsewhere. Core's own hints, fixes and help lines now name its commands
this way too, and the setup screen's doctor hint leaves out
`--profile default` when no profile was picked, as doctor's own lines do.
