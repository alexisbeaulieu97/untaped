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
