An install's reference now lists the plugins it composes (`plugins`, written
even when empty), by `setup register` and kept current by the install's
starts: each one compares the file with what it composes and rewrites it when
they differ, so one that couldn't is retried by the next. A command waits for
the references' lock first (warning if it stays busy), so a prune elsewhere
can't miss it; root `--help` and `--version` don't wait. So `setup prune`
keeps the folder of a plugin another install sharing the home uses, and keeps
every folder while a live install's reference has no list. `write_reference`
in `untaped.sdk.testing` takes `plugins=` (left out, the key is too).
