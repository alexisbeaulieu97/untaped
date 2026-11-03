"""What another plugin may import from ansible: the ``CheckoutSource`` contract and its record.

A plugin whose records check out repositories (job templates, say) fills
:class:`CheckoutSource` and lists its provider in its plugin class's
``fills``; ``ansible impact checkouts ROLE`` then shows them under the
dependents they check out.
"""

from __future__ import annotations

from untaped_ansible.checkouts import Checkout, CheckoutSource

__all__ = ["Checkout", "CheckoutSource"]
