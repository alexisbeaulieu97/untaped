**Breaking (plugins):** `untaped.contracts` is now `untaped.sdk.contracts`
and `untaped.testing` is now `untaped.sdk.testing`; core's other modules
moved under `untaped.sdk`, `untaped.core` and `untaped.runtime`. A plugin
imports the names `untaped.sdk` exports, `untaped.sdk.contracts` and
`untaped.sdk.testing`, as before.
