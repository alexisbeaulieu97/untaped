`untaped.sdk.contracts.providing({name: classes})` takes provider classes
(each a `Provider` subclass of the contract it fills) and no owner string:
it composes a plugin per key whose `fills` are those classes, each filling
the contract it subclasses, as an installed plugin's `fills` do. A class that
is no provider raises `TypeError` naming it.
