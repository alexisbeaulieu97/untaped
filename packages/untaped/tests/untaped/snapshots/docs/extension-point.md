# acme extensions rack
untaped VERSION · untaped-acme · ENV
How these pages are organized: `untaped docs`.

Where the widgets you can name in acme commands come from.

## Filled by
- store: The widgets the store has in stock.

## How to fill it

Fill it with a provider that lists the widgets it holds; acme asks every
provider and merges what they answer.

## Methods

- `widgets() -> list[Widget]` (required): Every widget the provider holds.
- `find(name: str) -> Widget | None` (optional): The widget called `name`, if the provider holds one.
- `placed(plan: Plan) -> list[Widget]` (optional): The widgets `plan` adds that the provider does not hold yet.

## Types

### `Widget`

One widget on a rack.

Kind: `acme.widget`; its page: `untaped docs acme outputs widget`.

| Field | Type | Description |
| --- | --- | --- |
| `name` | text, required | Its name, unique on the rack. |
| `size` | size | How much room it takes. |
| `tags` | list of text | Labels to find it by. |

### `Widget.size`

| Field | Type | Description |
| --- | --- | --- |
| `width` | integer, default `1` | Across, in slots. |
| `height` | integer, default `1` | Up, in slots. |
| `depth` | integer, default `0` | Back, in slots; 0 is flat. |

### `Plan`

A plan of widgets to add to one rack.

Kind: `acme.plan`; its page: `untaped docs acme inputs plan`.

| Field | Type | Description |
| --- | --- | --- |
| `rack` | text, required | The rack the widgets go on. |
| `widgets` | list of widget | The widgets to add. |

### `Plan.widgets[]`

| Field | Type | Description |
| --- | --- | --- |
| `name` | text, required | Its name, unique on the rack. |
| `size` | size | How much room it takes. |
| `tags` | list of text | Labels to find it by. |

### `Plan.widgets[].size`

| Field | Type | Description |
| --- | --- | --- |
| `width` | integer, default `1` | Across, in slots. |
| `height` | integer, default `1` | Up, in slots. |
| `depth` | integer, default `0` | Back, in slots; 0 is flat. |
