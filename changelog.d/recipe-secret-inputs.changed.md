**Breaking (users):** a recipe input's `sensitive:` is now `secret:`, with no
old spelling, and `recipe get` rows name the field `secret`. `recipe apply
--var` also takes a `list` input as comma-separated text
(`--var cols=name,owner`), and a run missing several required inputs names
them all at once. Its `--var`, `--vars-file` and `--non-interactive` list
under their own help panel, linking `untaped docs values guides vars`.
