# ``git_corpus`` is imported from its own module: it loads the git plugin's
# store, which nothing that only needs the client (``untaped_github.api``) pays for.
from untaped_github.infrastructure.github_client import GithubClient

__all__ = ["GithubClient"]
