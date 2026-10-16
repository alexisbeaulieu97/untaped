`untaped ansible impact ROLE --uses VAR` lists the places the role's
dependents define, set or read a variable, one `ansible.variable_use_hit`
row each, with the file, line, YAML key path and how it is used. Repeat
`--uses` for any of several names. A dependent whose files were never
analysed is an error line (exit 1) rather than a missing row, and a role that
defines none of the names gets a hint.
