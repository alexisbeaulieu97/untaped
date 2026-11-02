**Breaking (scripts):** `untaped setup plan` rows replace `by` (`user` or
`agent`) with `secret`, true when the step takes a secret; failure records
drop `fix_by`, and a hint always prints as `hint:`, never `hint (your step):`.
