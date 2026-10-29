`awx` reads, `delete` and `edit` say `No job templates found.` (the kind's
own words) when the selection matches nothing, only in a table, so `-f json`
prints `[]` with nothing on stderr; they said `No matching JobTemplate
found.` in every format. An ambiguous lookup's advice is its `hint:`, and its
messages follow the house style (lowercase, no period).
