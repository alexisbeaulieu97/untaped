Of lint's guides, `writing-rules` is now `guides rule-module` and `lint-file`
is `untaped docs lint inputs lint_file`. A script that parsed a lint table
for repeated blank targets reads every row's target, or passes `-f json`. A
plugin that imported `untaped_lint.SPEC` or `untaped_lint.build_app` names
the plugin class `untaped_lint.plugin:LintPlugin`; what other plugins may
import stays `untaped_lint.api`.
