"""Ready-made screen components: inputs, choices, tabs, buttons, lists, forms and layout.

Import what you need from the module that owns it; nothing is re-exported here,
so importing the package loads neither Rich nor prompt_toolkit. Every
component is a frozen dataclass with the same three parts (see
:class:`~untaped.screen.components.fields.Field`): a constructor that is its
``init``, ``update(message)`` and ``view(frame, focused=..., width=...)``.
"""
