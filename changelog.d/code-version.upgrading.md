Replace `version=lambda: code_version("acme.parser")` with
`version=installed_code("acme.parser")`, imported from `untaped.sdk`. It
hashes every file of each module or package it names; name a library's
package (`"yaml"`) where `distributions=` named its distribution. A named
module that is not installed raises instead of counting as absent, so leave
an optional library out. `extra=` has no counterpart: combine the strings in
your own function (`lambda: f"{installed_code('acme.parser')()}:v2"`).
