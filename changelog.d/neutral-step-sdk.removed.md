**Breaking (plugins):** `UntapedError` and every SDK error drop `fix_by`
(the attribute and the keyword), and `untaped.sdk.errors.fix_by()` and
`FixBy` are gone; an error's hint names its step on its own.
