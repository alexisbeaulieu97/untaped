# acme inputs plan
untaped VERSION · untaped-acme · ENV
How these pages are organized: `untaped docs`.

A plan of widgets to add to one rack.

Each widget listed is added unless one by its name is there.

Kind: `acme.plan`.
Read by: `untaped acme widgets add`.
Starter: `untaped acme widgets start`.

## Fields

| Field | Type | Description |
| --- | --- | --- |
| `rack` | text, required | The rack the widgets go on. |
| `widgets` | list of widget | The widgets to add. |

### `widgets[]`

| Field | Type | Description |
| --- | --- | --- |
| `name` | text, required | Its name, unique on the rack. |
| `size` | size | How much room it takes. |
| `tags` | list of text | Labels to find it by. |

### `widgets[].size`

| Field | Type | Description |
| --- | --- | --- |
| `width` | integer, default `1` | Across, in slots. |
| `height` | integer, default `1` | Up, in slots. |
| `depth` | integer, default `0` | Back, in slots; 0 is flat. |
