**Breaking (sdk):** a `Record` type whose fields would lose data in a JSON
round trip (a secret, an excluded field, an alias validation does not accept,
a computed field or a custom serializer) fails when it is defined, and a kind
whose first segment holds an underscore is rejected.
