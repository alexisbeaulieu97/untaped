**Breaking (plugins):** `untaped.contracts` is now `untaped.sdk.contracts`
and `untaped.testing` is now `untaped.sdk.testing`; core's other modules
moved under `untaped.sdk`, `untaped.core` and `untaped.runtime`. Each SDK
name has one spelling: a group's names (caches, state rows, directories,
data files and the file helpers in `storage`; the HTTP client and
credentials in `network`; doctor checks and setup marks in `health`; the
editor, prompts and picker in `prompt`; `git`, `projects`, `contracts`) are
imported from the group only, and `untaped.sdk` keeps what a plugin's own
files need (the plugin, its commands and fields, records and words,
errors). The screen names leave the SDK for `untaped.screen`, and names only
core used (`read_stdin`, `table_row`, `HttpSettings`, `connected_client`,
`Written`, `HolderState`, the holds' bookkeeping) are no longer exported;
every name left has a first-party user or says who its first will be.
