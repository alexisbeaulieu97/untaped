`untaped_github.api.GithubClient`'s batched ref probes run `concurrency`
chunks at once and report progress through `on_progress(done, total)`. A
chunk whose request fails for any reason but a GraphQL error gives its repos
a `request` failure, with the error's `category`, instead of stopping the
probe; a GraphQL error still stops it. `batch_default_branch_refs` asks for
100 repos a request, not 200, and `rate_limit_remaining` is the lowest
budget any response reported.
