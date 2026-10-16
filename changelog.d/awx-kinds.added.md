`untaped plugin schema` describes every record awx writes: each catalog
resource (`awx.job_template`, `awx.project` and the other thirteen), `awx.job`
and `awx.unified_template` list the fields untaped reads "plus any others"
(`additionalProperties`), since a resource row is AWX's own answer; and the
rows awx builds, from `awx.event`, `awx.log` and `awx.launch_outcome` to
`awx.document`, `awx.test_case` and `awx.prune_outcome`, which it could not
find before.
