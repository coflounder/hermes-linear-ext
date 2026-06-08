"""hermes-linear-ext — Linear Agent Sessions for Hermes Agent (zero-patch plugin).

A Hermes platform plugin: drop into ``$HERMES_HOME/plugins/linear`` and enable
with ``hermes plugins enable linear-platform``. Exposes a top-level ``register``
that the Hermes plugin loader calls with a ``PluginContext``.
"""
from .adapter import register

__all__ = ["register"]
__version__ = "0.2.0"
