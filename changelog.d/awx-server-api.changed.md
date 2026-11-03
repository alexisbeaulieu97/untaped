`untaped awx` detects whether the server runs AWX or AAP, and which release,
on first use, and talks to its controller API where that release serves it
(behind the platform gateway from AAP 2.5). `awx.server_version` (`AWX
24.6.1`, `AAP controller 4.6.2`) says it instead of detecting; `awx ping
--refresh` detects again. A controller untaped-awx doesn't serve (AWX before
18) exits 4, and `untaped doctor` gains the `awx.server-api` row.
