A deprecated Python API a plugin uses while a command runs warns again: a
deprecated SDK function, a marked parameter and a behaviour's `warn` each
print their `untaped.deprecation` line once per run. Python hides
deprecation warnings raised outside `__main__`, so none of them ever
reached the terminal; untaped now shows its own while a command runs, and a
filter you set that matches them (`-W`, `PYTHONWARNINGS`, a test
harness's) still decides first. A deprecated function's
`warnings.deprecated` names `category=UntapedDeprecationWarning`, which
`untaped lint` checks (`lint.deprecation-text`), and a parameter's record
`id` names its function's module, as its surface line does.
