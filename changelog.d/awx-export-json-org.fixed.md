`awx export -f json` keeps an org-less record's `organization: null`, and
`awx apply` reads the JSON list it prints, so `export -f json | apply -` no
longer moves such a record into the default organization.
