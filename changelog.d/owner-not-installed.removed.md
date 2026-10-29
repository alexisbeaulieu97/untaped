**Breaking (plugins):** `Reason.owner_not_installed` (`owner-not-installed`)
is gone from `untaped.sdk.contracts`. A plugin requires the owner of every
contract it fills, so a fill whose owner is not composed is set aside
silently, and `doctor` no longer shows a `contract-provider` row for it.
