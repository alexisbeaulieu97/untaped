Drop `awx.api_prefix` from a profile: untaped detects AWX or AAP on first
use. Where detection can't reach the server's `ping/`, set
`awx.server_version`, for example `untaped config set awx.server_version
"AAP controller 4.6.2"`.
