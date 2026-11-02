**Breaking (plugins):** before importing a plugin, untaped checks everything
its distribution requires against what is installed: `untaped` and other
plugins by version, any other package by presence. One that isn't met, or a
required plugin that is disabled, disables that plugin alone
(`unmet-requirement`) with the fix; installing a missing library is enough
to bring it back. A plugin whose module raises on import is `import-failed`,
not `malformed-entry-point`, and the `owner-out-of-range` contract reason is
gone. Running a disabled plugin's command exits 4 with the reason instead of
reporting an unknown command.
