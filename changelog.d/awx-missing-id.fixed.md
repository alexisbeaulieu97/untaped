An integer reference to an AWX resource that doesn't exist is reported as
missing: `awx lint FILES` gives an `awx.missing-reference` finding (exit 3)
instead of stopping, and `apply` and `get --by-id` say `Inventory not found
(id=999)` instead of printing the raw URL.
