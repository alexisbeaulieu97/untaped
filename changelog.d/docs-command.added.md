`untaped docs` prints the documentation of the untaped you run, generated from
what core and each plugin declare: `untaped docs` explains the addresses and
lists every plugin, `untaped docs PLUGIN` what to read first and its sections,
and `untaped docs PLUGIN commands|inputs|outputs|settings|extensions|guides
[NAME]` one page each. `--search TEXT` prints every matching line with its
page's address, or says on stderr that none matched (a blank one exits 2); an
unknown address exits 2 naming the page that lists what exists. Pages render
at a terminal and are raw Markdown when piped.
