Calling `connected_client` without `base_url=`, which reads `base_url` and
`token` off the settings by name, and its `required=`, `base_url_field=` and
`bearer_token_field=` parameters are deprecated and removed in 12.0; pass the
values (`base_url=`, `bearer=`, `require_token=`) instead. Both still work and
warn; passing both kinds is an error.
