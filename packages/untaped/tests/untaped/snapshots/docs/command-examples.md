# acme commands widgets show
untaped VERSION · untaped-acme · ENV
How these pages are organized: `untaped docs`.

List the widgets picked.

Read first: `untaped docs acme guides selection`.

## Usage
`untaped acme widgets show [OPTIONS]`

### Selection
- `--every`: Every widget on the rack.
- `--names`: The widgets named.

### Options
- `--limit`: Show at most this many. Default: `10`.

### Output
- `--format`, `-f`: The output format. One of `table`, `json`, `yaml`, `pipe`, `raw`.
- `--columns`, `-c`: Table columns: a list, or +NAME to add.

## Outputs
Rows of `acme.widget` (`untaped docs acme outputs widget`): One widget on a rack.
- `name` (text, required): Its name, unique on the rack.
- `size` (size): How much room it takes.
- `tags` (list of text): Labels to find it by.
- Nested fields: `untaped docs acme outputs widget`.

## Examples

Every widget, as JSON:

```console
$ untaped acme widgets show --every -f json
```
