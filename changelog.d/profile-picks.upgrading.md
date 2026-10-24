Scripts that read `profile list`'s `active` column or `profile show`'s
`active` key read `selected`; scripts that read `profile current --format
json` read `profiles[].source` instead of a single `source`. A script that set
`UNTAPED_PROFILE` to make `config set` write into a profile now passes
`--profile NAME`. Rename any profile whose name has spaces, commas or a
leading `.` or `-`.
