A record field marked `Following` (a list of models) prints in table format
as a table of its own after the first, its columns read from the items'
model and headed by the field's name in words (`pull_requests` as
`pull requests`); among several rows, each row's table follows and its
heading names the row's `Key` value (`comments of ABC-1`). json, yaml and
pipe keep the field inside the record. Composition refuses `Following` on a
field that is not a list of models.
