"""Use cases for ``untaped awx lint``: the loaders that fill an ``AwxResources`` snapshot.

One loader per origin: :mod:`.documents` for apply documents (``FILES``,
the repository reader, ``untaped_awx.testing``) and :mod:`.controller` for
the controller's records. Both resolve references in :mod:`.resolution`.
"""
