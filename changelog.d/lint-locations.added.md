`untaped_lint.api.lint()` (experimental) takes `locations=`: it reports only
the findings at those locations, written as the table writes them
(`job_templates/14`, `templates.yml#2`), so a plugin can narrow a run to
exact resources where two share a target.
