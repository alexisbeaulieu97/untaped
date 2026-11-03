In a plugin's `pyproject.toml`, write each requirement on `untaped` or another
plugin as a floor: `untaped>=12.0` instead of `untaped>=12,<13`, the owner of
a contract you fill as `untaped-git>=12.0`. Ignores and allow comments
naming `lint.provides-requirement` name `lint.requirement-floor`.
