A record field marked `Following` (a list of models) prints in table format
as a second table after the first, headed by the field's name, its columns
read from the items' model;
json, yaml and pipe keep it inside the record. Composition refuses
`Following` on a field that is not a list of models.
