github's `RepoSource` waits for an inventory scope only to list repos: piped
`github.repo` rows become `git.repo` rows with no scope and no token, and a
selection needs only a token. Without a token github is no longer asked to
list or select (before, it was asked and failed on the missing token): a name
lookup says "github wasn't asked: no token" and how to log in. Doctor's
provider row names the waiting methods (`repos waits for
github.default_org`).
