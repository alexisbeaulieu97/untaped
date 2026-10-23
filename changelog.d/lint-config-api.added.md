`untaped_lint.api.lint_config(start)` (experimental) builds the
configuration `lint()` takes (the profile's `lint.ignore` and the repository
lint file found from `start`), so a plugin's own lint command applies the
same ignores as `untaped lint` without reading lint's settings.
