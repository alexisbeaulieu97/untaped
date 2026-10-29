`untaped plugin new NAME --fills OWNER.CONTRACT` writes a 12.0 plugin: a
plugin class in `plugin.py` (the entry point names it) filling the contract,
and tests mirroring the package (`tests/untaped_<name>/test_plugin.py` and
`tests/untaped_<name>/providers/test_<owner>.py`).
