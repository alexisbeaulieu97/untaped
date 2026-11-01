Workspaces made by 12.0 pre-releases before this change aren't read: archive
or remove them with the pre-release first, or delete their folders. A script
that read `freed_bytes` from `workspace remove` reads the repo row's
`detail`; one that matched `skipped` there matches `refused`.
