`installed_code(*modules)` names the code a cache's value depends on:
`Cache(version=installed_code(parsers))` refreshes when a file of that
module or package changes, and `Cache(version=installed_code())` when
untaped or any plugin is installed, upgraded, reinstalled or, in an editable
install, edited. Neither form imports a module to find it.
