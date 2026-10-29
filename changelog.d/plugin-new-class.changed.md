**Breaking (plugin authors):** `untaped plugin new NAME --fills
OWNER.CONTRACT` writes a 12.0 plugin: a plugin class in `plugin.py` (the
entry point names it) filling the contract, the owner in the plain
dependencies with its range (no extra, since the provider subclasses the
owner's contract), and tests mirroring the package:
`tests/untaped_<name>/test_plugin.py` (lint and the surface snapshot) and
`tests/untaped_<name>/providers/test_<owner>.py` (`assert_fills`).
