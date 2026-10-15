`untaped plugin list` rows carry each plugin's `distribution`, `version`,
`package`, `source_dir` and, from a checkout, `tests_dir`; `untaped.sdk`
exports them as `Plugin` with `installed_plugins()`, and `untaped.testing`
exports `PluginCandidate`, `allows` and `import_targets` for a plugin's
checks.
