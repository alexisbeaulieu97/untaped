"""The contracts registry: who owns, offers and fills each contract in this run.

The SDK's contracts group is loaded first: its ``gather`` imports the
registry, and the registry imports the group's declarations, so entering
through the registry would otherwise meet a half-loaded group.
"""

import untaped.sdk.contracts  # noqa: F401 - load order, see above
