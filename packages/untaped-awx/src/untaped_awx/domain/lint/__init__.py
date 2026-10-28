"""What ``awx lint`` checks: the resources snapshot its rules read, the views and the rules.

Pure, like the rest of ``domain``: the loaders that fill an
:class:`~untaped_awx.domain.lint.resources.Resources` live in
``application/lint/``, and nothing here reads a file or the controller.
Only lint code imports this package, so no other awx command pays for
``untaped_lint``.
"""
