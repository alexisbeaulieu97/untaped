# acme commands widgets add
untaped VERSION · untaped-acme · ENV
How these pages are organized: `untaped docs`.

Add the widgets of plans. (asks first)

A widget already there is reported `unchanged`.

Read first: `untaped docs acme guides finish`.

## Usage
`untaped acme widgets add [PLANS...] [OPTIONS]`

- `PLANS...`: The plans to add.
- `--finish`: The finish each widget gets. One of `matte`, `gloss`. Default: `matte`.
- `--rack`: The rack to add to. One of `default`, `spare`. Default: `default`.

### Output
- `--format`, `-f`: The output format. One of `table`, `json`, `yaml`, `pipe`, `raw`.
- `--columns`, `-c`: Table columns: a list, or +NAME to add.

### Confirmation
- `--yes`, `-y`: Go ahead without asking.
- `--dry-run`: Show what would change and change nothing.

## Inputs
`acme.plan` (`untaped docs acme inputs plan`): A plan of widgets to add to one rack.
- `rack` (text, required): The rack the widgets go on.
- `widgets` (list of widget): The widgets to add.
- Nested fields: `untaped docs acme inputs plan`.

## Outputs
Rows of `acme.widget` (`untaped docs acme outputs widget`): One widget on a rack.
- `name` (text, required): Its name, unique on the rack.
- `size` (size): How much room it takes.
- `tags` (list of text): Labels to find it by.
- Nested fields: `untaped docs acme outputs widget`.
