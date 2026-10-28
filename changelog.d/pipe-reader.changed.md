**Breaking (users):** a piped row is read whole, as its kind's record, before
a command acts on it: one that is incomplete or of a kind no installed plugin
declares exits 1 naming its line. `untaped lint --stdin` stops on such a row
instead of checking the others. `awx test run --compare FILE` reads the
saved `--format json` output of a run, no longer `--format pipe`.
