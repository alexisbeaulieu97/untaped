"""The plugin checks: the rows an installed plugin is checked by.

:mod:`~untaped.core.plugin_check.check` builds and renders the rows
(conventions, registration, contracts, migrations);
:mod:`~untaped.core.plugin_check.rules` runs the rules that read a composed
plugin, each family in its own module. Core keeps these because they read the
composition; the rules that read only a plugin's files are untaped-lint's, and
the checks run without lint installed (:func:`untaped.sdk.testing.check`).
"""
