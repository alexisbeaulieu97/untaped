`untaped recipe sync` no longer undoes a pack that another command replaced
or removed while sync was fetching it: that pack gets a `failed` row and stays
as the other command left it.
