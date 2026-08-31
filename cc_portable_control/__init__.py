"""cc-remote packaging tooling.

This local package exists so ``cc_portable_control`` remains importable when the
repository root is on ``sys.path``; it no longer collides with the PyPI
``packaging`` distribution.
"""
