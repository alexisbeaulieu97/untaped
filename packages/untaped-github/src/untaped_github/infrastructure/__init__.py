# ``git_corpus`` is imported from its own module: it loads the git plugin's
# store, which nothing that only needs the API (``untaped_github.api``) pays for.
from untaped_github.infrastructure.rest_2022 import Rest2022

__all__ = ["Rest2022"]
