# acme outputs widget
untaped VERSION · untaped-acme · ENV
How these pages are organized: `untaped docs`.

One widget on a rack.

Kind: `acme.widget`.
Printed by: `untaped acme widgets show`, `untaped acme widgets add`.

## Fields

| Field | Type | Description |
| --- | --- | --- |
| `name` | text, required | Its name, unique on the rack. |
| `size` | size | How much room it takes. |
| `tags` | list of text | Labels to find it by. |

### `size`

| Field | Type | Description |
| --- | --- | --- |
| `width` | integer, default `1` | Across, in slots. |
| `height` | integer, default `1` | Up, in slots. |
| `depth` | integer, default `0` | Back, in slots; 0 is flat. |
