Move each guide file's text into the docstring of the option bundle or enum
it explains, its first paragraph saying when to read it; it is served at
`untaped docs PLUGIN guides NAME` with NAME the class name in kebab-case. A
`.untaped/lint.yml` that ignored `guide-name`, `guide-description` or
`guide-heading` drops those ids.
