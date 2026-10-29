`Output(format="raw")` gives a 12.0 command another default output format
than `table`, such as `raw` for a one-line answer or `yaml` for a document;
`--format` still overrides it, and only a `table` default follows
`UNTAPED_FORMAT` and `untaped.ui.format`. A format the `Output` does not offer
is refused when the class is defined. `Output(formats=("tree", "json"))`
offers only the formats it names, each core's or a renderer's, the first the
default; `--format` refuses the others.
